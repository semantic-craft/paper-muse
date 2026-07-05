import SwiftUI
import WebKit

/// 卡片墙 web 画布：WKWebView 加载 muse_server 同源托管的 /ui/。
/// 装一个名为 museBridge 的脚本消息通道：web 里点「深挖圆桌」→ 回调 onDrill。
struct CanvasWebView: NSViewRepresentable {
    let url: URL
    var onDrill: (DrillSeed) -> Void

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(context.coordinator, name: "museBridge")
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onDrill: onDrill) }

    final class Coordinator: NSObject, WKScriptMessageHandler {
        let onDrill: (DrillSeed) -> Void
        init(onDrill: @escaping (DrillSeed) -> Void) { self.onDrill = onDrill }

        func userContentController(_ controller: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "museBridge",
                  let body = message.body as? [String: Any],
                  (body["action"] as? String) == "drill",
                  let name = body["name"] as? String
            else { return }
            onDrill(DrillSeed(
                name: name,
                type: body["type"] as? String ?? "",
                mechanism: body["mechanism"] as? String ?? "",
                why: body["why"] as? String ?? ""
            ))
        }
    }
}

/// 被「深挖」的卡片 → Co-STORM 圆桌种子。
struct DrillSeed {
    let name: String
    let type: String
    let mechanism: String
    let why: String

    /// 卡名 + 机制拼成种子话题，给圆桌足够上下文起步。
    var topic: String {
        mechanism.isEmpty ? name : "\(name)：\(mechanism)"
    }
}
