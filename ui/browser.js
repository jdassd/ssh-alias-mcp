/* Local browser transport. The same form can still run in an MCP App host. */
(function () {
  "use strict";
  var tokenKey = "ssh-config-token";
  var fragment = new URLSearchParams(window.location.hash.slice(1));
  var token = fragment.get("token");
  if (token) {
    window.sessionStorage.setItem(tokenKey, token);
    window.history.replaceState(null, "", window.location.pathname);
  } else {
    token = window.sessionStorage.getItem(tokenKey);
  }

  var pendingReads = new Map();

  async function send(name, args, signal) {
      if (!token) throw new Error("配置会话已失效，请重新打开 SSH 服务器配置。");
      var response;
      try {
        response = await fetch("/api/tool", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Config-Token": token },
          body: JSON.stringify({ name: name, arguments: args || {} }),
          signal: signal
        });
      } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new Error("配置服务已关闭或暂不可用，请重新打开 SSH 服务器配置。");
      }
      if (!response.ok) {
        if (response.status === 403) {
          throw new Error("配置会话已失效，请重新打开 SSH 服务器配置。");
        }
        throw new Error("本机配置服务暂不可用（" + response.status + "）。");
      }
      return response.json();
  }

  window.sshConfigBrowser = {
    cancelDetailsExcept(server) {
      pendingReads.forEach(function (entry) {
        if (entry.name === "ssh_get_server_config" && entry.server !== server) {
          entry.controller.abort();
        }
      });
    },
    callTool(name, args) {
      var read = ["ssh_ui_bootstrap", "ssh_list_servers", "ssh_get_server_config"].includes(name);
      if (!read) return send(name, args);
      var key = name + ":" + JSON.stringify(args || {});
      var existing = pendingReads.get(key);
      if (existing && !existing.controller.signal.aborted) return existing.promise;
      var controller = new AbortController();
      var entry = { name: name, server: args && args.server, controller: controller };
      entry.promise = send(name, args, controller.signal).finally(function () {
        if (pendingReads.get(key) === entry) pendingReads.delete(key);
      });
      pendingReads.set(key, entry);
      return entry.promise;
    }
  };
}());
