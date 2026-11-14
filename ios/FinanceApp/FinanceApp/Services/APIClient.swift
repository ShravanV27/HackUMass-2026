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

    struct HealthResponse: Decodable, Sendable {
        let status: String
        let mode: String
    }

    func fetchHealth() async throws -> HealthResponse {
        try await get("/health")
    }

    func fetchDashboard(customerId: String = demoCustomerId) async throws -> DashboardResponse {
        try await get("/customers/\(customerId)/dashboard")
    }

    func fetchCashflowForecast(customerId: String = demoCustomerId) async throws -> CashflowForecastResponse {
        try await get("/customers/\(customerId)/predictions/cashflow?horizon_days=14&lookback_days=30")
    }

    func fetchSafeToSpend(customerId: String = demoCustomerId) async throws -> SafeToSpendResponse {
        try await get("/customers/\(customerId)/predictions/safe-to-spend?minimum_buffer=250.00")
    }

    func fetchSpendingDrift(customerId: String = demoCustomerId) async throws -> SpendingDriftResponse {
        try await get("/customers/\(customerId)/predictions/drift?recent_days=7&baseline_days=28")
    }

    func postWhatIf(
        amount: Decimal,
        description: String,
        customerId: String = demoCustomerId
    ) async throws -> WhatIfResponse {
        let body: [String: Any] = [
            "amount": NSDecimalNumber(decimal: amount).stringValue,
            "description": description,
            "minimum_buffer": "250.00",
            "horizon_days": 14,
        ]
        return try await post("/customers/\(customerId)/predictions/what-if", json: body)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15
        return try await send(request)
    }

    private func post<T: Decodable>(_ path: String, json: [String: Any]) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        request.timeoutInterval = 15
        return try await send(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.badStatus(-1)
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClientError.badStatus(http.statusCode)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIClientError.decoding(error)
        }
    }
}
