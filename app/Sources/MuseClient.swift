import Foundation

struct Turn: Decodable, Hashable {
    let role: String
    let utterance: String
}

struct StatusResponse: Decodable {
    let phase: String
    let topic: String?
    let progress: [String]
    let turns: [Turn]
    let output_dir: String?
    let error: String?
}

struct StepResponse: Decodable {
    let turns: [Turn]
}

struct ReportResponse: Decodable {
    let output_dir: String
    let files: [String]
}

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// muse_server.py 的极简 JSON 客户端。
struct MuseClient {
    var baseURL: URL { MuseServer.shared.baseURL }

    // retriever/fulltext 等新服务端参数暂不在此暴露，走服务端默认（tavily）——
    // 两幕剧重构的 web 画布设置区将接管（docs/superpowers/specs/2026-07-05-muse-two-act-design.md §10）
    func createSession(topic: String, model: String) async throws {
        struct Req: Encodable {
            let topic: String
            let model: String
        }
        _ = try await post("session", body: Req(topic: topic, model: model), timeout: 30)
    }

    func status() async throws -> StatusResponse {
        var req = URLRequest(url: baseURL.appendingPathComponent("status"))
        req.timeoutInterval = 10
        let (data, resp) = try await URLSession.shared.data(for: req)
        try Self.checkHTTP(data: data, resp: resp)
        return try JSONDecoder().decode(StatusResponse.self, from: data)
    }

    func step(utterance: String) async throws -> [Turn] {
        struct Req: Encodable { let utterance: String }
        let data = try await post("step", body: Req(utterance: utterance), timeout: 300)
        return try JSONDecoder().decode(StepResponse.self, from: data).turns
    }

    func report() async throws -> ReportResponse {
        struct Empty: Encodable {}
        let data = try await post("report", body: Empty(), timeout: 300)
        return try JSONDecoder().decode(ReportResponse.self, from: data)
    }

    // MARK: - helpers

    private func post<B: Encodable>(_ path: String, body: B, timeout: TimeInterval) async throws -> Data {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = timeout
        let (data, resp) = try await URLSession.shared.data(for: req)
        try Self.checkHTTP(data: data, resp: resp)
        return data
    }

    private static func checkHTTP(data: Data, resp: URLResponse) throws {
        guard let http = resp as? HTTPURLResponse, http.statusCode != 200 else { return }
        struct Detail: Decodable { let detail: String }
        let msg = (try? JSONDecoder().decode(Detail.self, from: data).detail)
            ?? String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
        throw APIError(message: msg)
    }
}
