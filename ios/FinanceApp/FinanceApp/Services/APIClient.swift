import Foundation

enum APIClientError: LocalizedError {
    case invalidURL
    case badStatus(Int)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid API URL."
        case .badStatus(let code):
            return "Server returned status \(code)."
        case .decoding(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        }
    }
}

actor APIClient {
    /// Fixed demo customer — server authenticates this identity; the client
    /// does not supply a separate auth token in M3.
    static let demoCustomerId = "demo-customer-001"

    /// Simulator reaches the Mac host via localhost.
    private let baseURL: URL

    init(baseURL: URL = URL(string: "http://127.0.0.1:8000")!) {
        self.baseURL = baseURL
    }

    func fetchDashboard(customerId: String = demoCustomerId) async throws -> DashboardResponse {
        guard let url = URL(string: "/customers/\(customerId)/dashboard", relativeTo: baseURL)?
            .absoluteURL
        else {
            throw APIClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.badStatus(-1)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClientError.badStatus(http.statusCode)
        }

        let decoder = JSONDecoder()
        do {
            return try decoder.decode(DashboardResponse.self, from: data)
        } catch {
            throw APIClientError.decoding(error)
        }
    }
}
