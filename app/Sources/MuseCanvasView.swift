import SwiftUI

/// 应用内容区外壳（两幕画布）。
/// 构思幕 = 卡片墙 WebView（两幕切换与对抗幕占位都在 web 里）；
/// 点卡「深挖圆桌」经 museBridge 切到原生圆桌（复用 v0.1 RoundtableView），以该卡为种子。
/// 壳只管：拉起本地引擎、加载态/错误态、卡片墙 ↔ 圆桌深挖的切换。
struct MuseCanvasView: View {
    @State private var phase: Phase = .loading
    @State private var drill: DrillSeed?
    @State private var errorText = ""

    enum Phase { case loading, ready, failed }

    var body: some View {
        Group {
            switch phase {
            case .loading:
                VStack(spacing: 14) {
                    ProgressView().controlSize(.large)
                    Text("正在启动本地引擎…").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            case .failed:
                VStack(spacing: 14) {
                    Text("后端启动失败").font(.headline)
                    Text(errorText)
                        .font(.caption).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).frame(maxWidth: 420)
                    Button("重试") { Task { await boot() } }
                        .controlSize(.large)
                }
                .padding(40)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            case .ready:
                if let seed = drill {
                    roundtable(seed)
                } else {
                    CanvasWebView(
                        url: MuseServer.shared.baseURL.appendingPathComponent("ui/"),
                        onDrill: { drill = $0 }
                    )
                }
            }
        }
        .task { await boot() }
    }

    /// 深挖圆桌：顶部返回条 + 复用的原生圆桌（换种子即重开）。
    private func roundtable(_ seed: DrillSeed) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Button { drill = nil } label: {
                    Label("返回卡片墙", systemImage: "chevron.left")
                }
                .buttonStyle(.borderless)
                Text(seed.name)
                    .font(.callout).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            Divider()
            RoundtableView(seedTopic: seed.topic).id(seed.topic)
        }
    }

    private func boot() async {
        phase = .loading
        do {
            try await MuseServer.shared.ensureRunning()
            phase = .ready
        } catch {
            errorText = error.localizedDescription
            phase = .failed
        }
    }
}
