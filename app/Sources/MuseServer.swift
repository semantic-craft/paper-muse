import Foundation

/// 管理本地 muse_server.py 进程：按需拉起、轮询就绪、退出时终止。
/// ponytail: 路径写死到 ~/Projects/paper-muse（与 anamra PaperToolsLauncher 同款取舍），
/// 要移仓库时改 rootDir 一处即可。
final class MuseServer {
    static let shared = MuseServer()

    let port = 8765
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    private var process: Process?
    private var rootDir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Projects/paper-muse")
    }

    enum ServerError: LocalizedError {
        case missingPython(String)
        case notReady

        var errorDescription: String? {
            switch self {
            case .missingPython(let p): return "找不到 Python：\(p)\n请先在 paper-muse 目录用 uv 建好 .venv"
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
        let python = rootDir.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            throw ServerError.missingPython(python.path)
        }
        let p = Process()
        p.executableURL = python
        p.arguments = ["muse_server.py", "--port", "\(port)"]
        p.currentDirectoryURL = rootDir
        try p.run()
        process = p
    }

    private func isHealthy() async -> Bool {
        var req = URLRequest(url: baseURL.appendingPathComponent("health"))
        req.timeoutInterval = 2
        guard let (_, resp) = try? await URLSession.shared.data(for: req) else { return false }
        return (resp as? HTTPURLResponse)?.statusCode == 200
    }
}
