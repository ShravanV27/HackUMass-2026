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

struct ScheduledBill: Decodable, Identifiable, Sendable {
    var id: String { billId }
    let billId: String
    let customerId: String
    let accountId: String
    let name: String
    let amount: Decimal
    let category: String
    let nextDueDate: String
    let cadence: String

    enum CodingKeys: String, CodingKey {
        case billId = "bill_id"
        case customerId = "customer_id"
        case accountId = "account_id"
        case name, amount, category, cadence
        case nextDueDate = "next_due_date"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        billId = try container.decode(String.self, forKey: .billId)
        customerId = try container.decode(String.self, forKey: .customerId)
        accountId = try container.decode(String.self, forKey: .accountId)
        name = try container.decode(String.self, forKey: .name)
        amount = try container.decodeFlexibleDecimal(forKey: .amount)
        category = try container.decode(String.self, forKey: .category)
        nextDueDate = try container.decode(String.self, forKey: .nextDueDate)
        cadence = try container.decode(String.self, forKey: .cadence)
    }
}

struct CashflowDayPoint: Decodable, Identifiable, Sendable {
    var id: String { date }
    let date: String
    let projectedBalance: Decimal
    let scheduledBillTotal: Decimal
    let estimatedSpend: Decimal
    let scheduledBillNames: [String]

    enum CodingKeys: String, CodingKey {
        case date
        case projectedBalance = "projected_balance"
        case scheduledBillTotal = "scheduled_bill_total"
        case estimatedSpend = "estimated_spend"
        case scheduledBillNames = "scheduled_bill_names"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decode(String.self, forKey: .date)
        projectedBalance = try container.decodeFlexibleDecimal(forKey: .projectedBalance)
        scheduledBillTotal = try container.decodeFlexibleDecimal(forKey: .scheduledBillTotal)
        estimatedSpend = try container.decodeFlexibleDecimal(forKey: .estimatedSpend)
        scheduledBillNames = try container.decode([String].self, forKey: .scheduledBillNames)
    }
}

struct CashflowForecastResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let horizonDays: Int
    let startingBalance: Decimal
    let dailyEstimatedSpend: Decimal
    let scheduledBillsInHorizon: [ScheduledBill]
    let estimatedSpendTotal: Decimal
    let scheduledBillsTotal: Decimal
    let endingBalance: Decimal
    let minBalance: Decimal
    let points: [CashflowDayPoint]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case horizonDays = "horizon_days"
        case startingBalance = "starting_balance"
        case dailyEstimatedSpend = "daily_estimated_spend"
        case scheduledBillsInHorizon = "scheduled_bills_in_horizon"
        case estimatedSpendTotal = "estimated_spend_total"
        case scheduledBillsTotal = "scheduled_bills_total"
        case endingBalance = "ending_balance"
        case minBalance = "min_balance"
        case points
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        horizonDays = try container.decode(Int.self, forKey: .horizonDays)
        startingBalance = try container.decodeFlexibleDecimal(forKey: .startingBalance)
        dailyEstimatedSpend = try container.decodeFlexibleDecimal(forKey: .dailyEstimatedSpend)
        scheduledBillsInHorizon = try container.decode([ScheduledBill].self, forKey: .scheduledBillsInHorizon)
        estimatedSpendTotal = try container.decodeFlexibleDecimal(forKey: .estimatedSpendTotal)
        scheduledBillsTotal = try container.decodeFlexibleDecimal(forKey: .scheduledBillsTotal)
        endingBalance = try container.decodeFlexibleDecimal(forKey: .endingBalance)
        minBalance = try container.decodeFlexibleDecimal(forKey: .minBalance)
        points = try container.decode([CashflowDayPoint].self, forKey: .points)
    }
}

struct SafeToSpendResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let horizonDays: Int
    let availableBalance: Decimal
    let pendingExpenses: Decimal
    let pendingIncome: Decimal
    let scheduledBillsTotal: Decimal
    let estimatedSpendTotal: Decimal
    let minimumBuffer: Decimal
    let safeToSpend: Decimal

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case horizonDays = "horizon_days"
        case availableBalance = "available_balance"
        case pendingExpenses = "pending_expenses"
        case pendingIncome = "pending_income"
        case scheduledBillsTotal = "scheduled_bills_total"
        case estimatedSpendTotal = "estimated_spend_total"
        case minimumBuffer = "minimum_buffer"
        case safeToSpend = "safe_to_spend"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        horizonDays = try container.decode(Int.self, forKey: .horizonDays)
        availableBalance = try container.decodeFlexibleDecimal(forKey: .availableBalance)
        pendingExpenses = try container.decodeFlexibleDecimal(forKey: .pendingExpenses)
        pendingIncome = try container.decodeFlexibleDecimal(forKey: .pendingIncome)
        scheduledBillsTotal = try container.decodeFlexibleDecimal(forKey: .scheduledBillsTotal)
        estimatedSpendTotal = try container.decodeFlexibleDecimal(forKey: .estimatedSpendTotal)
        minimumBuffer = try container.decodeFlexibleDecimal(forKey: .minimumBuffer)
        safeToSpend = try container.decodeFlexibleDecimal(forKey: .safeToSpend)
    }
}

struct SpendingDriftResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let recentDays: Int
    let baselineDays: Int
    let recentSpend: Decimal
    let baselineSpend: Decimal
    let driftAmount: Decimal
    let driftPct: Decimal
    let direction: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case recentDays = "recent_days"
        case baselineDays = "baseline_days"
        case recentSpend = "recent_spend"
        case baselineSpend = "baseline_spend"
        case driftAmount = "drift_amount"
        case driftPct = "drift_pct"
        case direction
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        recentDays = try container.decode(Int.self, forKey: .recentDays)
        baselineDays = try container.decode(Int.self, forKey: .baselineDays)
        recentSpend = try container.decodeFlexibleDecimal(forKey: .recentSpend)
        baselineSpend = try container.decodeFlexibleDecimal(forKey: .baselineSpend)
        driftAmount = try container.decodeFlexibleDecimal(forKey: .driftAmount)
        driftPct = try container.decodeFlexibleDecimal(forKey: .driftPct)
        direction = try container.decode(String.self, forKey: .direction)
    }
}

struct WhatIfResponse: Decodable, Sendable {
    let customerId: String
    let purchaseAmount: Decimal
    let description: String
    let safeToSpendBefore: Decimal
    let safeToSpendAfter: Decimal
    let affordable: Bool
    let projectedMinBalanceAfter: Decimal
    let note: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case purchaseAmount = "purchase_amount"
        case description
        case safeToSpendBefore = "safe_to_spend_before"
        case safeToSpendAfter = "safe_to_spend_after"
        case affordable
        case projectedMinBalanceAfter = "projected_min_balance_after"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        purchaseAmount = try container.decodeFlexibleDecimal(forKey: .purchaseAmount)
        description = try container.decode(String.self, forKey: .description)
        safeToSpendBefore = try container.decodeFlexibleDecimal(forKey: .safeToSpendBefore)
        safeToSpendAfter = try container.decodeFlexibleDecimal(forKey: .safeToSpendAfter)
        affordable = try container.decode(Bool.self, forKey: .affordable)
        projectedMinBalanceAfter = try container.decodeFlexibleDecimal(forKey: .projectedMinBalanceAfter)
        note = try container.decode(String.self, forKey: .note)
    }
}
