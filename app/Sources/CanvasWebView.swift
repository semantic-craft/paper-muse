import SwiftUI
import WebKit
import AppKit

/// Web 画布：WKWebView 加载 muse_server 同源托管的 /ui/（卡片墙 + 圆桌两幕都在 web 内）。
/// museBridge 通道：web 里「在访达打开」报告 → 原生用 Finder 定位产物目录
/// （唯一必须回到原生的动作——web 打不开 Finder）。
struct CanvasWebView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(context.coordinator, name: "museBridge")
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKScriptMessageHandler {
        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "museBridge",
                  let body = message.body as? [String: Any] else { return }
            let action = body["action"] as? String
            guard let path = body["path"] as? String else { return }
            if action == "reveal" {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            } else if action == "open" {
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
            }
        }
    }
}
