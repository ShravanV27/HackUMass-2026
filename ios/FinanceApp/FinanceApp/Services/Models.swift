import Foundation

/// FastAPI/Pydantic serializes Decimal as JSON strings; Swift's default
/// Decimal decode only accepts numbers. Accept both.
private extension KeyedDecodingContainer {
    func decodeFlexibleDecimal(forKey key: Key) throws -> Decimal {
        if let asString = try? decode(String.self, forKey: key) {
            guard let value = Decimal(string: asString) else {
                throw DecodingError.dataCorruptedError(
                    forKey: key,
                    in: self,
                    debugDescription: "Invalid decimal string: \(asString)"
                )
            }
            return value
        }
        if let asDouble = try? decode(Double.self, forKey: key) {
            return Decimal(asDouble)
        }
        return try decode(Decimal.self, forKey: key)
    }
}

struct Account: Decodable, Identifiable, Sendable {
    var id: String { accountId }
    let accountId: String
    let customerId: String
    let name: String
    let accountType: String
    let balance: Decimal
    let currency: String

    enum CodingKeys: String, CodingKey {
        case accountId = "account_id"
        case customerId = "customer_id"
        case name
        case accountType = "account_type"
        case balance
        case currency
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accountId = try container.decode(String.self, forKey: .accountId)
        customerId = try container.decode(String.self, forKey: .customerId)
        name = try container.decode(String.self, forKey: .name)
        accountType = try container.decode(String.self, forKey: .accountType)
        balance = try container.decodeFlexibleDecimal(forKey: .balance)
        currency = try container.decode(String.self, forKey: .currency)
    }
}

struct MoneyFlowSummary: Decodable, Sendable {
    let customerId: String
    let incomePosted: Decimal
    let expensesPosted: Decimal
    let pendingExpenses: Decimal
    let pendingIncome: Decimal
    let netPosted: Decimal
    let netIncludingPending: Decimal
    let transferVolume: Decimal

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case incomePosted = "income_posted"
        case expensesPosted = "expenses_posted"
        case pendingExpenses = "pending_expenses"
        case pendingIncome = "pending_income"
        case netPosted = "net_posted"
        case netIncludingPending = "net_including_pending"
        case transferVolume = "transfer_volume"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        incomePosted = try container.decodeFlexibleDecimal(forKey: .incomePosted)
        expensesPosted = try container.decodeFlexibleDecimal(forKey: .expensesPosted)
        pendingExpenses = try container.decodeFlexibleDecimal(forKey: .pendingExpenses)
        pendingIncome = try container.decodeFlexibleDecimal(forKey: .pendingIncome)
        netPosted = try container.decodeFlexibleDecimal(forKey: .netPosted)
        netIncludingPending = try container.decodeFlexibleDecimal(forKey: .netIncludingPending)
        transferVolume = try container.decodeFlexibleDecimal(forKey: .transferVolume)
    }
}

struct ForecastPoint: Decodable, Identifiable, Sendable {
    var id: String { date }
    let date: String
    let projectedBalance: Decimal

    enum CodingKeys: String, CodingKey {
        case date
        case projectedBalance = "projected_balance"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decode(String.self, forKey: .date)
        projectedBalance = try container.decodeFlexibleDecimal(forKey: .projectedBalance)
    }
}

struct ForecastSummary: Decodable, Sendable {
    let customerId: String
    let startingBalance: Decimal
    let dailyNetRate: Decimal
    let horizonDays: Int
    let points: [ForecastPoint]
    let mode: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case startingBalance = "starting_balance"
        case dailyNetRate = "daily_net_rate"
        case horizonDays = "horizon_days"
        case points
        case mode
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        startingBalance = try container.decodeFlexibleDecimal(forKey: .startingBalance)
        dailyNetRate = try container.decodeFlexibleDecimal(forKey: .dailyNetRate)
        horizonDays = try container.decode(Int.self, forKey: .horizonDays)
        points = try container.decode([ForecastPoint].self, forKey: .points)
        mode = try container.decode(String.self, forKey: .mode)
    }
}

struct DashboardResponse: Decodable, Sendable {
    let customerId: String
    let mode: String
    let accounts: [Account]
    let moneyFlow: MoneyFlowSummary
    let forecast: ForecastSummary
    let insight: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case mode
        case accounts
        case moneyFlow = "money_flow"
        case forecast
        case insight
    }
}
