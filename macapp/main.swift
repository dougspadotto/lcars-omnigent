import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webview: WKWebView!
    var lastLoad = Date.distantPast
    var pageStamp: String?
    var serverStamp: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        startBridgeIfNeeded()
        webview = WKWebView(frame: .zero)
        webview.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 80, y: 60, width: 1500, height: 950),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "LCARS Omnigent"
        window.contentView = webview
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        load()
        Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in
            if self.webview.url == nil { self.load() }
        }
        // update watcher : reload on UI changes, restart the bridge on its own
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in self.checkForUpdate() }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "Quit LCARS Omnigent", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        main.addItem(appItem)
        // edit menu : without it copy/paste/undo do not reach the webview
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        edit.addItem(NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "Z"))
        edit.addItem(.separator())
        edit.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        edit.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        edit.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        edit.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editItem.submenu = edit
        main.addItem(editItem)
        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadNow), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)
        viewItem.submenu = view
        main.addItem(viewItem)
        NSApp.mainMenu = main
    }

    @objc func reloadNow() { load() }

    func load() {
        lastLoad = Date()
        if let url = URL(string: "http://127.0.0.1:6768/") {
            webview.load(URLRequest(url: url))
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        startBridgeIfNeeded()
        if Date().timeIntervalSince(lastLoad) > 2 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.load() }
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // baseline the stamps from whatever the loaded page is running
        checkForUpdate()
    }

    func checkForUpdate() {
        guard let url = URL(string: "http://127.0.0.1:6768/api/build-stamp") else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let page = obj["page"], let server = obj["self"] else {
                // stamp unreachable : the bridge died after launch, bring it back
                DispatchQueue.main.async { self.startBridgeIfNeeded() }
                return
            }
            DispatchQueue.main.async {
                let pageS = "\(page)", serverS = "\(server)"
                guard self.serverStamp != nil else { self.serverStamp = serverS; self.pageStamp = pageS; return }
                if serverS != self.serverStamp {
                    // the bridge itself changed : restart it, then reload
                    self.serverStamp = serverS
                    self.pageStamp = pageS
                    self.restartBridge()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { self.load() }
                } else if pageS != self.pageStamp {
                    self.pageStamp = pageS
                    self.webview.reload()
                }
            }
        }.resume()
    }

    func restartBridge() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = ["-c", """
        /usr/bin/pkill -f 'lcars-omnigent/server.py' 2>/dev/null
        /bin/sleep 0.5
        PY="${LCARS_PYTHON:-}"
        [ -z "$PY" ] && PY="$(ls "$HOME"/.pyenv/versions/*/bin/python3 2>/dev/null | sort -V | tail -1)"
        [ -z "$PY" ] && PY="$(command -v python3 || true)"
        [ -z "$PY" ] && PY=/usr/bin/python3
        /usr/bin/nohup "$PY" "$HOME/code/lcars-omnigent/server.py" >> "$HOME/Library/Logs/lcars-bridge.log" 2>&1 &
        for i in $(seq 1 20); do
          /usr/bin/curl -s -o /dev/null --max-time 1 http://127.0.0.1:6768/ && break
          /bin/sleep 0.5
        done
        """]
        try? task.run()
    }

    func startBridgeIfNeeded() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = ["-c", """
        if ! /usr/bin/curl -s -o /dev/null --max-time 2 http://127.0.0.1:6768/; then
          PY="${LCARS_PYTHON:-}"
          [ -z "$PY" ] && PY="$(ls "$HOME"/.pyenv/versions/*/bin/python3 2>/dev/null | sort -V | tail -1)"
          [ -z "$PY" ] && PY="$(command -v python3 || true)"
          [ -z "$PY" ] && PY=/usr/bin/python3
          /usr/bin/nohup "$PY" "$HOME/code/lcars-omnigent/server.py" >> "$HOME/Library/Logs/lcars-bridge.log" 2>&1 &
          for i in $(seq 1 20); do
            /usr/bin/curl -s -o /dev/null --max-time 1 http://127.0.0.1:6768/ && break
            /bin/sleep 0.5
          done
        fi
        """]
        try? task.run()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
