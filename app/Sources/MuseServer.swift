import Foundation

/// 管理本地 muse_server.py 进程：按需拉起、轮询就绪、退出时终止。
final class MuseServer {
    static let shared = MuseServer()

    let port = 8765
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    private var process: Process?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private let fileManager = FileManager.default

    private struct LaunchPlan {
        let python: URL
        let arguments: [String]
        let currentDirectory: URL
        let environment: [String: String]
    }

    struct SetupStatus: Decodable {
        let setup_required: Bool
        let message: String
    }

    struct ReleaseHealth: Decodable {
        let state: String
        let blocking: Bool
        let message: String
    }

    private var devRootDir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Projects/paper-muse")
    }

    enum ServerError: LocalizedError {
        case missingPython(String, String)
        case missingServerAssets(String)
        case missingAppSupportDirectory
        case runtimeBootstrapFailed(String)
        case serverImportFailed(String)
        case notReady

        var errorDescription: String? {
            switch self {
            case .missingPython(let p, let hint): return "找不到 Python：\(p)\n\(hint)"
            case .missingServerAssets(let p): return "找不到打包后的后端资源：\(p)\nRelease 版本不会回退到开发 checkout。"
            case .missingAppSupportDirectory: return "找不到 Application Support 目录，无法准备 PaperMuse 用户数据目录"
            case .runtimeBootstrapFailed(let msg): return "PaperMuse runtime 安装失败：\(msg)"
            case .serverImportFailed(let msg): return "后端导入失败：\(msg)"
            case .notReady: return "后端 30 秒内未就绪，检查 muse_server.py 日志"
            }
        }
    }

    /// 已在跑（含手动起的实例）→ 直接复用；否则拉起并轮询到 /health 就绪。
    func ensureRunning() async throws {
        if await isHealthy() { return }
        try launch()
        for _ in 0..<75 {
            try? await Task.sleep(nanoseconds: 400_000_000)
            if await isHealthy() { return }
            if let p = process, !p.isRunning {
                throw ServerError.serverImportFailed(serverOutputTail())
            }
        }
        throw ServerError.notReady
    }

    func stop() {
        if let p = process, p.isRunning { p.terminate() }
        process = nil
    }

    func setupStatus() async throws -> SetupStatus {
        let (data, resp) = try await URLSession.shared.data(from: baseURL.appendingPathComponent("setup/status"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw ServerError.notReady }
        return try JSONDecoder().decode(SetupStatus.self, from: data)
    }

    /// 应用内首配：把所选 provider 的 key POST 给本地引擎写入 secrets.toml（引擎自己知道 dev/release 该写哪、并把 provider 记为圆桌默认）。
    func saveSecrets(provider: String, apiKey: String, tavily: String) async throws {
        let field: String
        switch provider {
        case "openai": field = "openai_api_key"
        case "gemini": field = "google_api_key"
        default: field = "deepseek_api_key"
        }
        var payload: [String: String] = ["provider": provider]
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let t = tavily.trimmingCharacters(in: .whitespacesAndNewlines)
        if !key.isEmpty { payload[field] = key }
        if !t.isEmpty { payload["tavily_api_key"] = t }
        var request = URLRequest(url: baseURL.appendingPathComponent("setup/secrets"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (_, resp) = try await URLSession.shared.data(for: request)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw ServerError.notReady }
    }

    private func launch() throws {
        let plan = try launchPlan()
        let p = Process()
        p.executableURL = plan.python
        p.arguments = plan.arguments
        p.currentDirectoryURL = plan.currentDirectory
        var environment = ProcessInfo.processInfo.environment
        plan.environment.forEach { environment[$0.key] = $0.value }
        p.environment = environment
        let out = Pipe()
        let err = Pipe()
        p.standardOutput = out
        p.standardError = err
        stdoutPipe = out
        stderrPipe = err
        try p.run()
        process = p
    }

    func releaseHealth() async throws -> ReleaseHealth {
        let (data, resp) = try await URLSession.shared.data(from: baseURL.appendingPathComponent("release/health"))
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw ServerError.notReady }
        return try JSONDecoder().decode(ReleaseHealth.self, from: data)
    }

    private func launchPlan() throws -> LaunchPlan {
        if let serverRoot = bundledServerRoot() {
            return try releaseLaunchPlan(serverRoot: serverRoot)
        }
#if DEBUG
        return try developmentLaunchPlan()
#else
        let expected = Bundle.main.resourceURL?.appendingPathComponent("server", isDirectory: true).path ?? "Contents/Resources/server"
        throw ServerError.missingServerAssets(expected)
#endif
    }

    private func bundledServerRoot() -> URL? {
        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        let serverRoot = resourceURL.appendingPathComponent("server", isDirectory: true)
        let script = serverRoot.appendingPathComponent("muse_server.py")
        return fileManager.fileExists(atPath: script.path) ? serverRoot : nil
    }

    private func developmentLaunchPlan() throws -> LaunchPlan {
        let python = devRootDir.appendingPathComponent(".venv/bin/python")
        guard fileManager.isExecutableFile(atPath: python.path) else {
            throw ServerError.missingPython(python.path, "请先在 paper-muse 目录用 uv 建好 .venv")
        }
        return LaunchPlan(
            python: python,
            arguments: ["muse_server.py", "--port", "\(port)"],
            currentDirectory: devRootDir,
            environment: [:]
        )
    }

    private func releaseLaunchPlan(serverRoot: URL) throws -> LaunchPlan {
        let supportRoot = try appSupportRoot()
        let dataDir = supportRoot.appendingPathComponent("data", isDirectory: true)
        let configDir = supportRoot.appendingPathComponent("config", isDirectory: true)
        let cacheDir = supportRoot.appendingPathComponent("cache", isDirectory: true)
        let runtimeDir = supportRoot.appendingPathComponent("runtime", isDirectory: true)
        let logsDir = supportRoot.appendingPathComponent("logs", isDirectory: true)
        for dir in [dataDir, configDir, cacheDir, runtimeDir, logsDir] {
            try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        try installConfigTemplate(from: serverRoot, to: configDir)
        try bootstrapRuntime(serverRoot: serverRoot, runtimeDir: runtimeDir)

        let python = runtimeDir.appendingPathComponent("main/bin/python")
        guard fileManager.isExecutableFile(atPath: python.path) else {
            throw ServerError.missingPython(python.path, "需要先安装 PaperMuse runtime；不会回退到开发 checkout。")
        }

        return LaunchPlan(
            python: python,
            arguments: [
                "muse_server.py",
                "--port", "\(port)",
                "--release-mode",
                "--server-root", serverRoot.path,
                "--app-data-dir", dataDir.path,
                "--config-dir", configDir.path,
                "--cache-dir", cacheDir.path,
                "--runtime-dir", runtimeDir.path,
                "--logs-dir", logsDir.path,
            ],
            currentDirectory: serverRoot,
            environment: [
                // The bundle is code-signed and must never be mutated by Python imports.
                "PYTHONDONTWRITEBYTECODE": "1",
                "PAPER_MUSE_SERVER_ROOT": serverRoot.path,
                "PAPER_MUSE_APP_DATA_DIR": dataDir.path,
                "PAPER_MUSE_CONFIG_DIR": configDir.path,
                "PAPER_MUSE_CACHE_DIR": cacheDir.path,
                "PAPER_MUSE_RUNTIME_DIR": runtimeDir.path,
                "PAPER_MUSE_LOGS_DIR": logsDir.path,
            ]
        )
    }

    private func bootstrapRuntime(serverRoot: URL, runtimeDir: URL) throws {
        let script = serverRoot.appendingPathComponent("tools/runtime_bootstrap.py")
        let manifest = serverRoot.appendingPathComponent("runtime-manifest.json")
        guard fileManager.fileExists(atPath: script.path), fileManager.fileExists(atPath: manifest.path) else {
            throw ServerError.runtimeBootstrapFailed("缺少 runtime bootstrap 工具或 manifest")
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [
            script.path,
            "bootstrap",
            "--manifest", manifest.path,
            "--runtime-dir", runtimeDir.path,
        ]
        let out = Pipe()
        let err = Pipe()
        p.standardOutput = out
        p.standardError = err
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else {
            let data = err.fileHandleForReading.readDataToEndOfFile()
            let fallback = out.fileHandleForReading.readDataToEndOfFile()
            let msg = String(data: data.isEmpty ? fallback : data, encoding: .utf8) ?? "unknown error"
            throw ServerError.runtimeBootstrapFailed(msg.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    private func installConfigTemplate(from serverRoot: URL, to configDir: URL) throws {
        let src = serverRoot.appendingPathComponent("secrets.toml.example")
        let dst = configDir.appendingPathComponent("secrets.toml.example")
        if fileManager.fileExists(atPath: src.path) && !fileManager.fileExists(atPath: dst.path) {
            try fileManager.copyItem(at: src, to: dst)
        }
    }

    private func appSupportRoot() throws -> URL {
        guard let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            throw ServerError.missingAppSupportDirectory
        }
        return base.appendingPathComponent("PaperMuse", isDirectory: true)
    }

    private func isHealthy() async -> Bool {
        var req = URLRequest(url: baseURL.appendingPathComponent("health"))
        req.timeoutInterval = 2
        guard let (_, resp) = try? await URLSession.shared.data(for: req) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }

    private func serverOutputTail() -> String {
        let stdout = stdoutPipe?.fileHandleForReading.readDataToEndOfFile() ?? Data()
        let stderr = stderrPipe?.fileHandleForReading.readDataToEndOfFile() ?? Data()
        let combined = [stderr, stdout]
            .compactMap { String(data: $0, encoding: .utf8) }
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if combined.isEmpty { return "进程已退出，但没有 stderr/stdout 输出" }
        return String(combined.suffix(2000))
    }
}
