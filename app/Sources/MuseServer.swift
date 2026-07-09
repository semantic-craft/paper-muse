import Foundation

/// 管理本地 muse_server.py 进程：按需拉起、轮询就绪、退出时终止。
final class MuseServer {
    static let shared = MuseServer()

    let port = 8765
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    private var process: Process?
    private let fileManager = FileManager.default

    private struct LaunchPlan {
        let python: URL
        let arguments: [String]
        let currentDirectory: URL
        let environment: [String: String]
    }

    private var devRootDir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Projects/paper-muse")
    }

    enum ServerError: LocalizedError {
        case missingPython(String, String)
        case missingServerAssets(String)
        case missingAppSupportDirectory
        case notReady

        var errorDescription: String? {
            switch self {
            case .missingPython(let p, let hint): return "找不到 Python：\(p)\n\(hint)"
            case .missingServerAssets(let p): return "找不到打包后的后端资源：\(p)\nRelease 版本不会回退到开发 checkout。"
            case .missingAppSupportDirectory: return "找不到 Application Support 目录，无法准备 PaperMuse 用户数据目录"
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
        }
        throw ServerError.notReady
    }

    func stop() {
        if let p = process, p.isRunning { p.terminate() }
        process = nil
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
        try p.run()
        process = p
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
        for dir in [dataDir, configDir, cacheDir, runtimeDir] {
            try fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        }

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
            ],
            currentDirectory: serverRoot,
            environment: [
                "PAPER_MUSE_SERVER_ROOT": serverRoot.path,
                "PAPER_MUSE_APP_DATA_DIR": dataDir.path,
                "PAPER_MUSE_CONFIG_DIR": configDir.path,
                "PAPER_MUSE_CACHE_DIR": cacheDir.path,
                "PAPER_MUSE_RUNTIME_DIR": runtimeDir.path,
            ]
        )
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
}
