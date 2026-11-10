import AppKit
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate {
    var window: NSWindow!
    var process: Process?
    func applicationDidFinishLaunching(_ notification: Notification) {
        let repo = Bundle.main.bundleURL.deletingLastPathComponent()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900), styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        window.title = "PubMed GraRec Notion"; window.center(); window.delegate = self
        let web = WKWebView(frame: window.contentView!.bounds)
        web.uiDelegate = self
        web.autoresizingMask = [.width,.height]; window.contentView!.addSubview(web)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        let task = Process(); let pipe = Pipe()
        task.executableURL = repo.appendingPathComponent(".venv/bin/python")
        task.arguments = [repo.appendingPathComponent("scripts/grarec_app.py").path]
        task.currentDirectoryURL = repo; task.standardOutput = pipe; task.standardError = Pipe()
        process = task
        task.terminationHandler = { _ in DispatchQueue.main.async {
            if NSApp.isRunning { web.loadHTMLString("<h2>アプリを終了しました</h2><p>別の処理が起動中でないか確認し、再度開いてください。初回はSetupPaperPipeline.commandで環境を準備してください。</p>", baseURL: nil) }
        }}
        do { try task.run() } catch { web.loadHTMLString("<h2>起動できません</h2><p>SetupPaperPipeline.commandで環境を準備してください。</p>", baseURL: nil); return }
        DispatchQueue.global().async {
            let data = pipe.fileHandleForReading.availableData
            guard let line = String(data: data, encoding: .utf8)?.components(separatedBy: "\n").first, let url = URL(string: line), url.host == "127.0.0.1" else { return }
            DispatchQueue.main.async { web.load(URLRequest(url: url)) }
        }
        let menu = NSMenu(); let item = NSMenuItem(); menu.addItem(item)
        let sub = NSMenu(); sub.addItem(withTitle: "終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"); item.submenu = sub
        let editItem = NSMenuItem(); menu.addItem(editItem); let edit = NSMenu(title: "編集"); editItem.submenu = edit
        edit.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "貼り付け", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "すべて選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        NSApp.mainMenu = menu
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedFileTypes = ["txt"]
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { process?.terminate() }
}
let app = NSApplication.shared
let delegate = AppDelegate(); app.delegate = delegate
app.setActivationPolicy(.regular); app.run()
