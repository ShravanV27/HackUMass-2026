import Foundation

enum APIClientError: LocalizedError {
    case invalidURL
    case badStatus(Int, String?)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid API URL."
        case .badStatus(let code, let detail):
            if let detail, !detail.isEmpty {
                return "Server \(code): \(detail)"
            }
            return "Server returned status \(code)."
        case .decoding(let error):
            return "Failed to decode response: \(error.localizedDescription)"
        }
    }
}

actor APIClient {
    /// Fixed app customer — server authenticates this identity.
    static let demoCustomerId = "demo-customer-001"

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

    func fetchAccounts(customerId: String = demoCustomerId) async throws -> AccountsResponse {
        try await get("/customers/\(customerId)/accounts")
    }

    func fetchTransactions(customerId: String = demoCustomerId) async throws -> TransactionsResponse {
        try await get("/customers/\(customerId)/transactions")
    }

    func fetchCashflowForecast(
        horizonDays: Int = 14,
        lookbackDays: Int = 30,
        customerId: String = demoCustomerId
    ) async throws -> CashflowForecastResponse {
        try await get(
            "/customers/\(customerId)/predictions/cashflow?horizon_days=\(horizonDays)&lookback_days=\(lookbackDays)"
        )
    }

    func fetchSafeToSpend(
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> SafeToSpendResponse {
        let buffer = NSDecimalNumber(decimal: minimumBuffer).stringValue
        return try await get(
            "/customers/\(customerId)/predictions/safe-to-spend?horizon_days=\(horizonDays)&minimum_buffer=\(buffer)"
        )
    }

    func fetchSpendingDrift(customerId: String = demoCustomerId) async throws -> SpendingDriftResponse {
        try await get("/customers/\(customerId)/predictions/drift?recent_days=7&baseline_days=28")
    }

    func fetchOverdraftRisk(
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> OverdraftRiskResponse {
        let buffer = NSDecimalNumber(decimal: minimumBuffer).stringValue
        return try await get(
            "/customers/\(customerId)/predictions/overdraft-risk?horizon_days=\(horizonDays)&minimum_buffer=\(buffer)"
        )
    }

    func fetchBehavior(customerId: String = demoCustomerId) async throws -> BehaviorSegmentResponse {
        try await get("/customers/\(customerId)/predictions/behavior")
    }

    func postWhatIf(
        amount: Decimal,
        description: String,
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        purchaseDate: String? = nil,
        customerId: String = demoCustomerId
    ) async throws -> WhatIfResponse {
        var body: [String: Any] = [
            "amount": NSDecimalNumber(decimal: amount).stringValue,
            "description": description,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "horizon_days": horizonDays,
        ]
        if let purchaseDate, !purchaseDate.isEmpty {
            body["purchase_date"] = purchaseDate
        }
        return try await post("/customers/\(customerId)/predictions/what-if", json: body)
    }

    func postScenarios(
        scenarios: [[String: Any]],
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> ScenarioSimulationResponse {
        let body: [String: Any] = [
            "horizon_days": horizonDays,
            "lookback_days": 30,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "scenarios": scenarios,
        ]
        return try await post("/customers/\(customerId)/predictions/scenarios", json: body)
    }

    func fetchPaymentCycles(customerId: String = demoCustomerId) async throws -> PaymentCycleSettingsResponse {
        try await get("/customers/\(customerId)/settings/payment-cycles")
    }

    func putPaymentCycles(
        _ config: PaymentCycleConfig,
        customerId: String = demoCustomerId
    ) async throws -> PaymentCycleSettingsResponse {
        let data = try JSONEncoder().encode(config)
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        return try await put("/customers/\(customerId)/settings/payment-cycles", json: object)
    }

    func fetchDemoStatus(customerId: String = demoCustomerId) async throws -> DemoStatusResponse {
        try await get("/customers/\(customerId)/demo/status")
    }

    func fetchSession() async throws -> SessionResponse {
        try await get("/session")
    }

    func bindCustomer(nessieCustomerId: String) async throws -> SessionResponse {
        try await post("/session/bind-customer", json: [
            "nessie_customer_id": nessieCustomerId,
        ])
    }

    func postDemoPurchase(
        accountId: String,
        amount: Decimal,
        description: String,
        status: String,
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> DemoActionResponse {
        try await post("/customers/\(customerId)/demo/purchase", json: [
            "account_id": accountId,
            "amount": NSDecimalNumber(decimal: amount).stringValue,
            "description": description,
            "status": status,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "horizon_days": horizonDays,
        ])
    }

    func postDemoDeposit(
        accountId: String,
        amount: Decimal,
        description: String,
        status: String,
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> DemoActionResponse {
        try await post("/customers/\(customerId)/demo/deposit", json: [
            "account_id": accountId,
            "amount": NSDecimalNumber(decimal: amount).stringValue,
            "description": description,
            "status": status,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "horizon_days": horizonDays,
        ])
    }

    func postDemoBill(
        accountId: String,
        payee: String,
        amount: Decimal,
        paymentDate: String?,
        recurringDate: Int?,
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> DemoActionResponse {
        var body: [String: Any] = [
            "account_id": accountId,
            "payee": payee,
            "amount": NSDecimalNumber(decimal: amount).stringValue,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "horizon_days": horizonDays,
        ]
        if let paymentDate {
            body["payment_date"] = paymentDate
        }
        if let recurringDate {
            body["recurring_date"] = recurringDate
        }
        return try await post("/customers/\(customerId)/demo/bill", json: body)
    }

    func postSync(customerId: String = demoCustomerId) async throws -> SyncResponse {
        try await post("/customers/\(customerId)/sync", json: [:])
    }

    func fetchAlerts(
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> AlertsResponse {
        let buffer = NSDecimalNumber(decimal: minimumBuffer).stringValue
        return try await get(
            "/customers/\(customerId)/alerts?horizon_days=\(horizonDays)&minimum_buffer=\(buffer)"
        )
    }

    func fetchInsights(
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> InsightsResponse {
        let buffer = NSDecimalNumber(decimal: minimumBuffer).stringValue
        return try await get(
            "/customers/\(customerId)/insights?horizon_days=\(horizonDays)&minimum_buffer=\(buffer)"
        )
    }

    func postCopilot(
        question: String,
        includeScenario: Bool = false,
        scenarioName: String? = nil,
        scenarioAmount: Decimal? = nil,
        horizonDays: Int = 14,
        minimumBuffer: Decimal = 250,
        customerId: String = demoCustomerId
    ) async throws -> CopilotResponse {
        var body: [String: Any] = [
            "question": question,
            "include_scenario": includeScenario,
            "minimum_buffer": NSDecimalNumber(decimal: minimumBuffer).stringValue,
            "horizon_days": horizonDays,
        ]
        // Intentionally omit scenario_name by default — the API parses the
        // question so a purchase ask is not forced into cut_discretionary_20.
        if let scenarioName {
            body["scenario_name"] = scenarioName
        }
        if let scenarioAmount {
            body["scenario_amount"] = NSDecimalNumber(decimal: scenarioAmount).stringValue
        }
        return try await post("/customers/\(customerId)/copilot", json: body)
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 60
        return try await send(request)
    }

    private func post<T: Decodable>(_ path: String, json: [String: Any]) async throws -> T {
        try await sendJSON(path: path, method: "POST", json: json)
    }

    private func put<T: Decodable>(_ path: String, json: [String: Any]) async throws -> T {
        try await sendJSON(path: path, method: "PUT", json: json)
    }

    private func sendJSON<T: Decodable>(path: String, method: String, json: [String: Any]) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIClientError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        request.timeoutInterval = 60
        return try await send(request)
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.badStatus(-1, nil)
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8)
            throw APIClientError.badStatus(http.statusCode, detail)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIClientError.decoding(error)
        }
    }
}
