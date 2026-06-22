// MCP Bridge Plugin for Super Productivity — HTTP IPC edition

const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const BRIDGE_PORT_START = 27833;
const BRIDGE_PORT_END   = 27841; // exclusive

class MCPBridgePlugin {
  constructor() {
    this.bridgeUrl = null;  // set by setupBridgeConnection()
    this.commandWatchInterval = null;
    this.lastNoCommandsLog = 0;
    this.isInitialized = false;

    this.config = {
      commandCheckIntervalMs: 2000,
      logLevel: 'info',
    };

    this.stats = {
      commandsProcessed: 0,
      lastCommandTime: null,
      errors: 0,
      startTime: Date.now()
    };

    this.connectionState = 'connecting';
    this._backoffMs = 2000;
    this._backoffStart = null;
    this._retryTimeout = null;
    this._consecutivePollErrors = 0;

    this.logSeq = 0;
    this.logBuffer = [];
  }

  // ── HTTP helpers ─────────────────────────────────────────────────────────

  async _get(path) {
    if (!this.bridgeUrl) throw new Error('Bridge not connected');
    const res = await fetch(`${this.bridgeUrl}${path}`);
    if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
    return res.json();
  }

  async _post(path, body) {
    if (!this.bridgeUrl) throw new Error('Bridge not connected');
    const res = await fetch(`${this.bridgeUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
    return res.json();
  }

  // ── Reconnect state machine ───────────────────────────────────────────────

  _connectWithBackoff(isReconnect = false) {
    this._backoffStart = this._backoffStart || Date.now();
    if (!isReconnect) {
      this._backoffStart = Date.now();
      this._backoffMs = 2000;
    }
    this._attemptConnect(isReconnect);
  }

  async _attemptConnect(isReconnect) {
    this.connectionState = isReconnect ? 'reconnecting' : 'connecting';
    const label = isReconnect ? '⚠️ Reconnecting' : 'Connecting';
    this.updateUI({ status: { type: this.connectionState, message: `${label}…` } });

    // Try last-known port first when reconnecting
    const portsToTry = [];
    if (isReconnect && this.bridgeUrl) {
      try {
        const lastPort = parseInt(new URL(this.bridgeUrl).port);
        portsToTry.push(lastPort);
      } catch (_) {}
    }
    for (let p = BRIDGE_PORT_START; p < BRIDGE_PORT_END; p++) {
      if (!portsToTry.includes(p)) portsToTry.push(p);
    }

    for (const port of portsToTry) {
      try {
        const url = `http://localhost:${port}`;
        const res = await fetch(`${url}/status`);
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'ok') {
            await this._onConnected(url);
            return;
          }
        }
      } catch (_) {}
    }
    this._scheduleRetry(isReconnect);
  }

  _scheduleRetry(isReconnect) {
    const elapsed = Date.now() - (this._backoffStart || Date.now());
    if (elapsed >= 5 * 60 * 1000) {
      this.connectionState = 'failed';
      this.updateUI({
        status: { type: 'failed', message: '❌ Bridge not found — click Reconnect' }
      });
      return;
    }
    const delay = this._backoffMs;
    this._backoffMs = Math.min(this._backoffMs * 2, 30000);
    const label = isReconnect ? '⚠️ Reconnecting' : 'Connecting';
    this.updateUI({
      status: {
        type: this.connectionState,
        message: `${label}… retry in ${Math.round(delay / 1000)}s`
      }
    });
    if (this._retryTimeout) clearTimeout(this._retryTimeout);
    this._retryTimeout = setTimeout(() => this._attemptConnect(isReconnect), delay);
  }

  async _onConnected(url) {
    this.bridgeUrl = url;
    this.connectionState = 'connected';
    this._backoffMs = 2000;
    this._backoffStart = null;
    this._consecutivePollErrors = 0;
    if (this._retryTimeout) {
      clearTimeout(this._retryTimeout);
      this._retryTimeout = null;
    }
    await this.loadConfig();
    this.startCommandProcessing();
    this.isInitialized = true;
    const port = new URL(url).port;
    await this.log(`Bridge connected on port ${port}`, 'info');
    this.updateUI({
      status: { type: 'connected', message: '✅ Connected and ready' },
      config: { pollingFrequency: Math.floor(this.config.commandCheckIntervalMs / 1000) }
    });
  }

  _onConnectionLost() {
    if (this.commandWatchInterval) {
      clearInterval(this.commandWatchInterval);
      this.commandWatchInterval = null;
    }
    this.isInitialized = false;
    this._consecutivePollErrors = 0;
    this.log('Connection lost — starting reconnect loop', 'warn');
    this._connectWithBackoff(true);
  }

  manualReconnect() {
    if (this._retryTimeout) clearTimeout(this._retryTimeout);
    this._retryTimeout = null;
    this._backoffMs = 2000;
    this._backoffStart = null;
    this._connectWithBackoff(true);
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  async init() {
    await this.log('MCP Bridge initializing…', 'info');
    this.registerUI();
    this.registerHooks();
    this._connectWithBackoff(false);
  }

  async loadConfig() {
    try {
      const cfg = await this._get('/config');
      this.config.commandCheckIntervalMs = cfg.commandCheckIntervalMs || 2000;
      this.config.logLevel = cfg.logLevel || 'info';
    } catch (e) {
      await this.log(`Config load failed (using defaults): ${e.message}`, 'warn');
    }
  }

  async saveConfig() {
    try {
      await this._post('/config', {
        commandCheckIntervalMs: this.config.commandCheckIntervalMs,
        logLevel: this.config.logLevel,
      });
    } catch (e) {
      await this.log(`Config save failed: ${e.message}`, 'warn');
    }
  }

  // ── Command polling ───────────────────────────────────────────────────────

  startCommandProcessing() {
    if (this.commandWatchInterval) clearInterval(this.commandWatchInterval);
    this.commandWatchInterval = setInterval(
      () => this.processNewCommands(),
      this.config.commandCheckIntervalMs
    );
  }

  async processNewCommands() {
    try {
      const commands = await this._get('/commands');
      this._consecutivePollErrors = 0;
      if (!Array.isArray(commands) || commands.length === 0) return;
      await this.log(`Processing ${commands.length} command(s)`, 'debug');
      for (const command of commands) {
        try {
          await this.executeCommand(command);
        } catch (error) {
          await this.log(`Command ${command.action} failed: ${error.message}`, 'error');
        }
      }
    } catch (error) {
      await this.log(`Poll error: ${error.message}`, 'error');
      this.stats.errors++;
      this._consecutivePollErrors++;
      if (this._consecutivePollErrors >= 3) {
        await this.log('3 consecutive poll failures — reconnecting', 'warn');
        this._onConnectionLost();
      }
    }
  }

  async writeCommandResponse(commandId, response) {
    try {
      await this._post(`/response/${commandId}`, response);
    } catch (e) {
      await this.log(`Response write failed: ${e.message}`, 'error');
    }
  }

  // ── Command dispatch ──────────────────────────────────────────────────────

  async executeCommand(command) {
    await this.log(`→ ${command.action}`, 'info');
    const argSummary = command.data
      ? JSON.stringify(command.data).slice(0, 120)
      : command.taskId ? `taskId=${command.taskId}` : '';
    if (argSummary) await this.log(`  args: ${argSummary}`, 'debug');

    const startTime = Date.now();
    let result;

    try {
      switch (command.action) {
        case 'getTasks':
          result = await PluginAPI.getTasks(); break;
        case 'getArchivedTasks':
          result = await PluginAPI.getArchivedTasks(); break;
        case 'getCurrentContextTasks':
          result = await PluginAPI.getCurrentContextTasks(); break;
        case 'addTask':
          if (command.data.parentId) {
            const { parentId, title, ...rest } = command.data;
            const hasSyntax = /[@#+]/.test(title);
            const createTitle = hasSyntax
              ? title.replace(/@\w+/g, '').replace(/#\w+/g, '').replace(/\+\w+/g, '').trim()
              : title;
            const subtaskId = await PluginAPI.addTask({ ...rest, title: createTitle, parentId });
            if (hasSyntax) await PluginAPI.updateTask(subtaskId, { title });
            result = subtaskId;
          } else {
            result = await PluginAPI.addTask(command.data);
          }
          break;
        case 'updateTask':
          result = await PluginAPI.updateTask(command.taskId, command.data); break;
        case 'deleteTask':
        case 'removeTask':
          result = { success: false, error: 'Task deletion not supported. Use updateTask({isDone: true}).' };
          break;
        case 'setTaskDone':
        case 'markTaskDone':
        case 'completeTask':
          result = await PluginAPI.updateTask(command.taskId, { isDone: true, doneOn: Date.now() }); break;
        case 'setTaskUndone':
        case 'markTaskUndone':
        case 'uncompleteTask':
          result = await PluginAPI.updateTask(command.taskId, { isDone: false, doneOn: null }); break;
        case 'addTimeToTask':
        case 'addTimeSpent': {
          const tasks = await PluginAPI.getTasks();
          const task = tasks.find(t => t.id === command.taskId);
          result = task
            ? await PluginAPI.updateTask(command.taskId, { timeSpent: task.timeSpent + (command.timeMs || 0) })
            : { error: 'Task not found' };
          break;
        }
        case 'setTimeEstimate':
          result = await PluginAPI.updateTask(command.taskId, { timeEstimate: command.timeMs || 0 }); break;
        case 'moveTaskToProject':
          result = await PluginAPI.updateTask(command.taskId, { projectId: command.projectId }); break;
        case 'addTagToTask': {
          const tasks = await PluginAPI.getTasks();
          const task = tasks.find(t => t.id === command.taskId);
          if (task) {
            const ids = [...task.tagIds];
            if (!ids.includes(command.tagId)) ids.push(command.tagId);
            result = await PluginAPI.updateTask(command.taskId, { tagIds: ids });
          } else {
            result = { error: 'Task not found' };
          }
          break;
        }
        case 'removeTagFromTask': {
          const tasks = await PluginAPI.getTasks();
          const task = tasks.find(t => t.id === command.taskId);
          result = task
            ? await PluginAPI.updateTask(command.taskId, { tagIds: task.tagIds.filter(id => id !== command.tagId) })
            : { error: 'Task not found' };
          break;
        }
        case 'getAllProjects':
          result = await PluginAPI.getAllProjects(); break;
        case 'addProject':
          result = await PluginAPI.addProject(command.data); break;
        case 'updateProject':
          result = await PluginAPI.updateProject(command.projectId, command.data); break;
        case 'getAllTags':
          result = await PluginAPI.getAllTags(); break;
        case 'addTag':
          result = await PluginAPI.addTag(command.data); break;
        case 'updateTag':
          result = await PluginAPI.updateTag(command.tagId, command.data); break;
        case 'showSnack':
          try {
            result = await PluginAPI.showSnack({ msg: command.message, type: command.snackType || 'SUCCESS' });
          } catch (e) {
            result = { success: true, fallback: true };
          }
          break;
        case 'persistDataSynced':
          result = await PluginAPI.persistDataSynced(command.key, command.data); break;
        case 'loadSyncedData':
          result = await PluginAPI.loadSyncedData(command.key); break;
        case 'batchOperation':
          result = await this.executeBatchOperation(command.operations); break;
        default:
          throw new Error(`Unknown command action: ${command.action}`);
      }

      const ms = Date.now() - startTime;
      await this.writeCommandResponse(command.id, { success: true, result, executionTime: ms, timestamp: Date.now() });
      this.stats.commandsProcessed++;
      this.stats.lastCommandTime = Date.now();
      await this.log(`✓ ${command.action} [${ms}ms]`, 'info');

    } catch (error) {
      await this.log(`✗ ${command.action}: ${error.message}`, 'error');
      await this.writeCommandResponse(command.id, { success: false, error: error.message, timestamp: Date.now() });
      this.stats.errors++;
    }
  }

  async executeBatchOperation(operations) {
    const results = [];
    for (const op of operations) {
      try {
        let result;
        switch (op.action) {
          case 'addTask':    result = await PluginAPI.addTask(op.data); break;
          case 'updateTask': result = await PluginAPI.updateTask(op.taskId, op.data); break;
          case 'addProject': result = await PluginAPI.addProject(op.data); break;
          default: throw new Error(`Unsupported batch op: ${op.action}`);
        }
        results.push({ success: true, result });
      } catch (e) {
        results.push({ success: false, error: e.message });
      }
    }
    return results;
  }

  // ── Hooks & UI ───────────────────────────────────────────────────────────

  registerHooks() {
    PluginAPI.registerHook('taskUpdate',        async (d) => this.sendEventToMCP('taskUpdate', d));
    PluginAPI.registerHook('taskComplete',      async (d) => this.sendEventToMCP('taskComplete', d));
    PluginAPI.registerHook('taskDelete',        async (d) => this.sendEventToMCP('taskDelete', d));
    PluginAPI.registerHook('currentTaskChange', async (d) => this.sendEventToMCP('currentTaskChange', d));
  }

  registerUI() {
    // SP auto-registers a sidebar entry for iFrame plugins via the manifest.
    // No explicit registerMenuEntry needed — that would create a duplicate.
  }

  async sendEventToMCP(eventType, eventData) {
    if (!this.isInitialized) return;
    try {
      await this._post('/events', { eventType, eventData, timestamp: Date.now(), source: 'super-productivity' });
    } catch (e) {
      await this.log(`Event send failed: ${e.message}`, 'warn');
    }
  }

  updateUI(data) {
    if (typeof window !== 'undefined' && window.postMessage) {
      try {
        window.postMessage({ type: 'mcp-bridge-update', data: { ...data, stats: this.stats, timestamp: Date.now() } }, '*');
      } catch (_) {}
    }
  }

  getStatus() {
    return {
      isInitialized: this.isInitialized,
      connectionState: this.connectionState,
      bridgeUrl: this.bridgeUrl,
      stats: this.stats,
      logBuffer: this.logBuffer,
      config: { pollingFrequency: Math.floor(this.config.commandCheckIntervalMs / 1000), logLevel: this.config.logLevel }
    };
  }

  async updatePollingFrequency(frequencySeconds) {
    const ms = frequencySeconds * 1000;
    if (ms >= 1000 && ms <= 60000) {
      this.config.commandCheckIntervalMs = ms;
      await this.saveConfig();
      this.startCommandProcessing();
      await this.log(`Polling updated to ${frequencySeconds}s`, 'info');
      return true;
    }
    return false;
  }

  async updateLogLevel(level) {
    if (!['debug', 'info', 'warn', 'error'].includes(level)) return false;
    this.config.logLevel = level;
    await this.saveConfig();
    await this.log(`Log level set to ${level}`, 'info');
    return true;
  }

  async log(message, level = 'info') {
    const threshold = LOG_LEVELS[this.config.logLevel] ?? 1;
    const msgLevel  = LOG_LEVELS[level] ?? 1;
    if (msgLevel < threshold) return;

    const entry = { id: ++this.logSeq, level, message, timestamp: Date.now() };
    this.logBuffer.push(entry);
    if (this.logBuffer.length > 200) this.logBuffer.shift();
    console.log(`[${new Date().toISOString()}] [${level.toUpperCase()}] MCP Bridge: ${message}`);
  }
}

// ── Bootstrap ──────────────────────────────────────────────────────────────

const mcpBridge = new MCPBridgePlugin();

const _start = () => mcpBridge.init().catch(console.error);
if (typeof plugin !== 'undefined' && typeof plugin.onReady === 'function') {
  plugin.onReady(_start);
} else {
  _start();
}

if (typeof plugin !== 'undefined' && typeof plugin.onUnload === 'function') {
  plugin.onUnload(() => {
    if (mcpBridge.commandWatchInterval) clearInterval(mcpBridge.commandWatchInterval);
  });
}

if (typeof window !== 'undefined') window.mcpBridge = mcpBridge;
