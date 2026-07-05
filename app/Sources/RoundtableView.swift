import SwiftUI

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: String
    let text: String
    var isUser: Bool { role == "你" }
    var isModerator: Bool {
        role.localizedCaseInsensitiveContains("moderator") || role.contains("主持")
    }
}

@MainActor
final class RoundtableViewModel: ObservableObject {
    enum Stage { case setup, warming, chatting }

    @Published var stage: Stage = .setup
    @Published var topic = ""
    @Published var usePro = false
    @Published var progressText = "正在准备…"
    @Published var messages: [ChatMessage] = []
    @Published var busy = false
    @Published var draft = ""
    @Published var outputDir: String?
    @Published var errorMessage: String?
    @Published var reportSaved = false

    private let client = MuseClient()

    func start() {
        let t = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        stage = .warming
        progressText = "正在启动本地引擎…"
        Task {
            do {
                try await MuseServer.shared.ensureRunning()
                try await client.createSession(
                    topic: t,
                    model: usePro ? "deepseek-v4-pro" : "deepseek-v4-flash"
                )
                await pollUntilReady()
            } catch {
                errorMessage = error.localizedDescription
                stage = .setup
            }
        }
    }

    private func pollUntilReady() async {
        while true {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let s = try? await client.status() else { continue }
            switch s.phase {
            case "error":
                errorMessage = "热身失败：\n\(String((s.error ?? "未知错误").suffix(600)))"
                stage = .setup
                return
            case "ready":
                messages = s.turns.map { ChatMessage(role: $0.role, text: $0.utterance) }
                outputDir = s.output_dir
                stage = .chatting
                return
            default:
                if let last = s.progress.last { progressText = zhProgress(last) }
            }
        }
    }

    func continueRound() { send(utterance: "") }

    func inject() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        send(utterance: text)
    }

    private func send(utterance: String) {
        guard !busy else { return }
        busy = true
        Task {
            do {
                let turns = try await client.step(utterance: utterance)
                messages.append(contentsOf: turns.map { ChatMessage(role: $0.role, text: $0.utterance) })
            } catch {
                errorMessage = error.localizedDescription
            }
            busy = false
        }
    }

    func finish() {
        guard !busy else { return }
        busy = true
        Task {
            do {
                let r = try await client.report()
                outputDir = r.output_dir
                reportSaved = true
            } catch {
                errorMessage = error.localizedDescription
            }
            busy = false
        }
    }

    func revealOutput() {
        guard let dir = outputDir else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: dir)])
    }

    func newTopic() {
        stage = .setup
        messages = []
        reportSaved = false
        topic = ""
    }

    private func zhProgress(_ s: String) -> String {
        if s.contains("Step 1") { return "① 组建专家团并检索资料…" }
        if s.contains("Step 2") { return "② 整理检索到的信息…" }
        if s.contains("Step 3") { return "③ 构建思维导图…" }
        if s.contains("Step 4") { return "④ 生成圆桌开场…" }
        return s
    }
}

struct RoundtableView: View {
    var seedTopic: String? = nil
    @StateObject private var vm = RoundtableViewModel()
    @State private var seeded = false

    var body: some View {
        Group {
            switch vm.stage {
            case .setup: setup
            case .warming: warming
            case .chatting: chat
            }
        }
        .alert(
            "出错了",
            isPresented: Binding(
                get: { vm.errorMessage != nil },
                set: { if !$0 { vm.errorMessage = nil } }
            )
        ) {
            Button("好") { vm.errorMessage = nil }
        } message: {
            Text(vm.errorMessage ?? "")
        }
        .onAppear {
            // 深挖入口：带种子话题进来时自动起圆桌，跳过手输 setup
            guard !seeded, let s = seedTopic, !s.isEmpty else { return }
            seeded = true
            vm.topic = s
            vm.start()
        }
    }

    private var setup: some View {
        VStack(spacing: 18) {
            Text("论文构思者 · 互动圆桌")
                .font(.title.bold())
            Text("输入论文主题或写作困惑，多位专家 + 主持人围绕它讨论；你随时插话转向。")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            TextField("例如：生成式人工智能的平台责任", text: $vm.topic, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 460)
                .onSubmit { vm.start() }
            Toggle("深度模式（v4-pro 推理模型，更慢但问题更刁钻）", isOn: $vm.usePro)
                .toggleStyle(.checkbox)
            Button("开始圆桌") { vm.start() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(vm.topic.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(40)
    }

    private var warming: some View {
        VStack(spacing: 16) {
            ProgressView()
                .controlSize(.large)
            Text(vm.topic)
                .font(.headline)
            Text(vm.progressText)
                .foregroundStyle(.secondary)
            Text("热身约 1-3 分钟：检索资料、组建专家团、构建思维导图")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(40)
    }

    private var chat: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(vm.messages) { m in
                            bubble(m).id(m.id)
                        }
                        if vm.busy {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("圆桌思考中…").foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .id("busy")
                        }
                    }
                    .padding()
                }
                .onChange(of: vm.messages.count) { _, _ in
                    if let last = vm.messages.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }
            Divider()
            HStack(spacing: 10) {
                TextField("插话、追问或转向…", text: $vm.draft, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { vm.inject() }
                Button("插话") { vm.inject() }
                    .disabled(vm.busy || vm.draft.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("让圆桌继续") { vm.continueRound() }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.busy)
            }
            .padding(12)
        }
        .navigationTitle(vm.topic.isEmpty ? "论文构思者" : vm.topic)
        .toolbar {
            ToolbarItemGroup {
                if vm.reportSaved {
                    Button("在访达中打开") { vm.revealOutput() }
                }
                Button(vm.reportSaved ? "再次出报告" : "结束并出报告") { vm.finish() }
                    .disabled(vm.busy)
                Button("新主题") { vm.newTopic() }
                    .disabled(vm.busy)
            }
        }
    }

    @ViewBuilder
    private func bubble(_ m: ChatMessage) -> some View {
        VStack(alignment: m.isUser ? .trailing : .leading, spacing: 4) {
            Text(m.role)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(m.text)
                .textSelection(.enabled)
                .padding(10)
                .background(bubbleColor(m), in: RoundedRectangle(cornerRadius: 10))
        }
        .frame(maxWidth: .infinity, alignment: m.isUser ? .trailing : .leading)
    }

    private func bubbleColor(_ m: ChatMessage) -> Color {
        if m.isUser { return Color.accentColor.opacity(0.18) }
        if m.isModerator { return Color.orange.opacity(0.15) }
        return Color(nsColor: .controlBackgroundColor)
    }
}
