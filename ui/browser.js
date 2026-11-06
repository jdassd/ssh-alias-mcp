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

  window.sshConfigBrowser = {
    async callTool(name, args) {
      if (!token) throw new Error("配置会话已失效，请重新打开 SSH 服务器配置。");
      var response = await fetch("/api/tool", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Config-Token": token },
        body: JSON.stringify({ name: name, arguments: args || {} })
      });
      if (!response.ok) {
        if (response.status === 403) {
          throw new Error("配置会话已失效，请重新打开 SSH 服务器配置。");
        }
        throw new Error("本机配置服务暂不可用（" + response.status + "）。");
      }
      return response.json();
    }
  };
}());
