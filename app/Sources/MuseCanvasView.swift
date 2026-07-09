import SwiftUI

/// 应用内容区：整块就是一个 WKWebView，加载本地 web 画布。
/// 卡片墙、圆桌深挖、对抗幕占位、明暗切换都在 web 内；壳只管拉起本地引擎 + 加载/错误态。
struct MuseCanvasView: View {
    @State private var phase: Phase = .loading
    @State private var errorText = ""
    @State private var setupText = ""

    enum Phase { case loading, ready, setupRequired, failed }

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

            case .setupRequired:
                VStack(spacing: 14) {
                    Text("需要完成首次设置").font(.headline)
                    Text(setupText)
                        .font(.caption).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center).frame(maxWidth: 520)
                    HStack {
                        Button("重新检查") { Task { await boot() } }
                        Button("先打开画布") { phase = .ready }
                    }
                    .controlSize(.large)
                }
                .padding(40)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            case .ready:
                CanvasWebView(url: MuseServer.shared.baseURL.appendingPathComponent("ui/"))
            }
        }
        .task { await boot() }
    }

    private func boot() async {
        phase = .loading
        do {
            try await MuseServer.shared.ensureRunning()
            if let setup = try? await MuseServer.shared.setupStatus(), setup.setup_required {
                setupText = setup.message
                phase = .setupRequired
            } else {
                phase = .ready
            }
        } catch {
            errorText = error.localizedDescription
            phase = .failed
        }
    }
}
