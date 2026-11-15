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
    let projectedIncome: Decimal
    let eventLabels: [String]

    enum CodingKeys: String, CodingKey {
        case date
        case projectedBalance = "projected_balance"
        case scheduledBillTotal = "scheduled_bill_total"
        case estimatedSpend = "estimated_spend"
        case scheduledBillNames = "scheduled_bill_names"
        case projectedIncome = "projected_income"
        case eventLabels = "event_labels"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decode(String.self, forKey: .date)
        projectedBalance = try container.decodeFlexibleDecimal(forKey: .projectedBalance)
        scheduledBillTotal = try container.decodeFlexibleDecimal(forKey: .scheduledBillTotal)
        estimatedSpend = try container.decodeFlexibleDecimal(forKey: .estimatedSpend)
        scheduledBillNames = try container.decodeIfPresent([String].self, forKey: .scheduledBillNames) ?? []
        projectedIncome = (try? container.decodeFlexibleDecimal(forKey: .projectedIncome)) ?? 0
        eventLabels = try container.decodeIfPresent([String].self, forKey: .eventLabels) ?? []
    }
}

struct CashflowEvent: Decodable, Identifiable, Sendable {
    var id: String { "\(date)-\(kind)-\(label)" }
    let date: String
    let kind: String
    let label: String
    let amount: Decimal

    enum CodingKeys: String, CodingKey {
        case date, kind, label, amount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decode(String.self, forKey: .date)
        kind = try container.decode(String.self, forKey: .kind)
        label = try container.decode(String.self, forKey: .label)
        amount = (try? container.decodeFlexibleDecimal(forKey: .amount)) ?? 0
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
    let daysUntilZero: Int?
    let overdraftRisk: String
    let incomeAssumed: Bool
    let projectedIncomeTotal: Decimal
    let nextEvents: [CashflowEvent]
    let assumptions: [String]
    let confidence: String
    /// Weekday cash-spend rates (`mon`…`sun`). Missing on older payloads.
    let weekdayRates: [String: Decimal]

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
        case daysUntilZero = "days_until_zero"
        case overdraftRisk = "overdraft_risk"
        case incomeAssumed = "income_assumed"
        case projectedIncomeTotal = "projected_income_total"
        case nextEvents = "next_events"
        case assumptions, confidence
        case weekdayRates = "weekday_rates"
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
        daysUntilZero = try container.decodeIfPresent(Int.self, forKey: .daysUntilZero)
        overdraftRisk = try container.decodeIfPresent(String.self, forKey: .overdraftRisk) ?? "none"
        incomeAssumed = try container.decodeIfPresent(Bool.self, forKey: .incomeAssumed) ?? false
        projectedIncomeTotal = (try? container.decodeFlexibleDecimal(forKey: .projectedIncomeTotal)) ?? 0
        nextEvents = try container.decodeIfPresent([CashflowEvent].self, forKey: .nextEvents) ?? []
        assumptions = try container.decodeIfPresent([String].self, forKey: .assumptions) ?? []
        confidence = try container.decodeIfPresent(String.self, forKey: .confidence) ?? "medium"
        if let asStrings = try? container.decode([String: String].self, forKey: .weekdayRates) {
            weekdayRates = asStrings.compactMapValues { Decimal(string: $0) }
        } else if let asDoubles = try? container.decode([String: Double].self, forKey: .weekdayRates) {
            weekdayRates = asDoubles.mapValues { Decimal($0) }
        } else {
            weekdayRates = [:]
        }
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
    let purchaseDate: String?
    let assumptions: [String]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case purchaseAmount = "purchase_amount"
        case description
        case safeToSpendBefore = "safe_to_spend_before"
        case safeToSpendAfter = "safe_to_spend_after"
        case affordable
        case projectedMinBalanceAfter = "projected_min_balance_after"
        case note
        case purchaseDate = "purchase_date"
        case assumptions
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
        purchaseDate = try container.decodeIfPresent(String.self, forKey: .purchaseDate)
        assumptions = try container.decodeIfPresent([String].self, forKey: .assumptions) ?? []
    }
}

struct AccountsResponse: Decodable, Sendable {
    let customerId: String
    let accounts: [Account]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case accounts
    }
}

struct PredictionSnapshot: Decodable, Sendable {
    let safeToSpend: Decimal
    let minProjectedBalance: Decimal
    let endingBalance: Decimal
    let overdraftRisk: String
    let daysUntilZero: Int?
    let scheduledBillsTotal: Decimal
    let estimatedSpendTotal: Decimal

    enum CodingKeys: String, CodingKey {
        case safeToSpend = "safe_to_spend"
        case minProjectedBalance = "min_projected_balance"
        case endingBalance = "ending_balance"
        case overdraftRisk = "overdraft_risk"
        case daysUntilZero = "days_until_zero"
        case scheduledBillsTotal = "scheduled_bills_total"
        case estimatedSpendTotal = "estimated_spend_total"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        safeToSpend = try container.decodeFlexibleDecimal(forKey: .safeToSpend)
        minProjectedBalance = try container.decodeFlexibleDecimal(forKey: .minProjectedBalance)
        endingBalance = try container.decodeFlexibleDecimal(forKey: .endingBalance)
        overdraftRisk = try container.decode(String.self, forKey: .overdraftRisk)
        daysUntilZero = try container.decodeIfPresent(Int.self, forKey: .daysUntilZero)
        scheduledBillsTotal = try container.decodeFlexibleDecimal(forKey: .scheduledBillsTotal)
        estimatedSpendTotal = try container.decodeFlexibleDecimal(forKey: .estimatedSpendTotal)
    }
}

struct DemoActionResponse: Decodable, Sendable {
    let customerId: String
    let mode: String
    let action: String
    let resourceId: String
    let accountId: String
    let amount: Decimal
    let description: String
    let synced: Bool
    let before: PredictionSnapshot
    let after: PredictionSnapshot
    let deltaSafeToSpend: Decimal
    let deltaMinBalance: Decimal
    let note: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case mode, action, description, synced, before, after, note
        case resourceId = "resource_id"
        case accountId = "account_id"
        case amount
        case deltaSafeToSpend = "delta_safe_to_spend"
        case deltaMinBalance = "delta_min_balance"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        mode = try container.decode(String.self, forKey: .mode)
        action = try container.decode(String.self, forKey: .action)
        resourceId = try container.decode(String.self, forKey: .resourceId)
        accountId = try container.decode(String.self, forKey: .accountId)
        amount = try container.decodeFlexibleDecimal(forKey: .amount)
        description = try container.decode(String.self, forKey: .description)
        synced = try container.decode(Bool.self, forKey: .synced)
        before = try container.decode(PredictionSnapshot.self, forKey: .before)
        after = try container.decode(PredictionSnapshot.self, forKey: .after)
        deltaSafeToSpend = try container.decodeFlexibleDecimal(forKey: .deltaSafeToSpend)
        deltaMinBalance = try container.decodeFlexibleDecimal(forKey: .deltaMinBalance)
        note = try container.decode(String.self, forKey: .note)
    }
}

struct DemoStatusResponse: Decodable, Sendable {
    let customerId: String
    let mode: String
    let writesEnabled: Bool
    let boundNessieCustomerId: String?
    let envNessieCustomerId: String?
    let message: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case mode, message
        case writesEnabled = "writes_enabled"
        case boundNessieCustomerId = "bound_nessie_customer_id"
        case envNessieCustomerId = "env_nessie_customer_id"
    }
}

struct SessionResponse: Decodable, Sendable {
    let appCustomerId: String
    let mode: String
    let boundNessieCustomerId: String?
    let writesEnabled: Bool
    let message: String

    enum CodingKeys: String, CodingKey {
        case appCustomerId = "app_customer_id"
        case mode, message
        case boundNessieCustomerId = "bound_nessie_customer_id"
        case writesEnabled = "writes_enabled"
    }
}

struct SyncResponse: Decodable, Sendable {
    let customerId: String
    let mode: String
    let status: String
    let message: String
    let accountsUpserted: Int
    let transactionsUpserted: Int

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case mode, status, message
        case accountsUpserted = "accounts_upserted"
        case transactionsUpserted = "transactions_upserted"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        mode = try container.decode(String.self, forKey: .mode)
        status = try container.decode(String.self, forKey: .status)
        message = try container.decode(String.self, forKey: .message)
        accountsUpserted = try container.decodeIfPresent(Int.self, forKey: .accountsUpserted) ?? 0
        transactionsUpserted = try container.decodeIfPresent(Int.self, forKey: .transactionsUpserted) ?? 0
    }
}

struct AlertItem: Decodable, Identifiable, Sendable {
    var id: String { alertId }
    let alertId: String
    let kind: String
    let severity: String
    let title: String
    let summary: String
    let evidence: [String: String]
    let relatedMetric: String?

    enum CodingKeys: String, CodingKey {
        case alertId = "alert_id"
        case kind, severity, title, summary, evidence
        case relatedMetric = "related_metric"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        alertId = try container.decode(String.self, forKey: .alertId)
        kind = try container.decode(String.self, forKey: .kind)
        severity = try container.decode(String.self, forKey: .severity)
        title = try container.decode(String.self, forKey: .title)
        summary = try container.decode(String.self, forKey: .summary)
        evidence = try container.decodeIfPresent([String: String].self, forKey: .evidence) ?? [:]
        relatedMetric = try container.decodeIfPresent(String.self, forKey: .relatedMetric)
    }
}

struct AlertsResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let mode: String
    let analyticsSource: String
    let alerts: [AlertItem]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case mode
        case analyticsSource = "analytics_source"
        case alerts
    }
}

struct InsightsResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let mode: String
    let aiGenerated: Bool
    let model: String?
    let alerts: [AlertItem]
    let alertExplanation: String
    let behaviorSegment: String?
    let coaching: String
    let metricGlossary: [String: String]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case mode
        case aiGenerated = "ai_generated"
        case model, alerts, coaching
        case alertExplanation = "alert_explanation"
        case behaviorSegment = "behavior_segment"
        case metricGlossary = "metric_glossary"
    }
}

struct CopilotResponse: Decodable, Sendable {
    let customerId: String
    let question: String
    let answer: String
    let aiGenerated: Bool
    let model: String?
    let groundedMetrics: [String: String]
    let scenarioNote: String?
    let refused: Bool

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case question, answer, model, refused
        case aiGenerated = "ai_generated"
        case groundedMetrics = "grounded_metrics"
        case scenarioNote = "scenario_note"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        question = try container.decode(String.self, forKey: .question)
        answer = try container.decode(String.self, forKey: .answer)
        aiGenerated = try container.decode(Bool.self, forKey: .aiGenerated)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        groundedMetrics = try container.decodeIfPresent([String: String].self, forKey: .groundedMetrics) ?? [:]
        scenarioNote = try container.decodeIfPresent(String.self, forKey: .scenarioNote)
        refused = try container.decodeIfPresent(Bool.self, forKey: .refused) ?? false
    }
}

struct BankTransaction: Decodable, Identifiable, Sendable {
    var id: String { transactionId }
    let transactionId: String
    let customerId: String
    let accountId: String
    let amount: Decimal
    let kind: String
    let status: String
    let description: String
    let category: String
    let postedAt: String

    enum CodingKeys: String, CodingKey {
        case transactionId = "transaction_id"
        case customerId = "customer_id"
        case accountId = "account_id"
        case amount, kind, status, description, category
        case postedAt = "posted_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        transactionId = try container.decode(String.self, forKey: .transactionId)
        customerId = try container.decode(String.self, forKey: .customerId)
        accountId = try container.decode(String.self, forKey: .accountId)
        amount = try container.decodeFlexibleDecimal(forKey: .amount)
        kind = try container.decode(String.self, forKey: .kind)
        status = try container.decode(String.self, forKey: .status)
        description = try container.decode(String.self, forKey: .description)
        category = try container.decode(String.self, forKey: .category)
        postedAt = try container.decode(String.self, forKey: .postedAt)
    }
}

struct TransactionsResponse: Decodable, Sendable {
    let customerId: String
    let accountId: String?
    let transactions: [BankTransaction]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case accountId = "account_id"
        case transactions
    }
}

struct OverdraftRiskResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let horizonDays: Int
    let minimumBuffer: Decimal
    let startingBalance: Decimal
    let minProjectedBalance: Decimal
    let endingBalance: Decimal
    let daysUntilZero: Int?
    let daysUntilBelowBuffer: Int?
    let overdraftRisk: String
    let suggestedDailyCutback: Decimal
    let incomeAssumed: Bool
    let note: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case horizonDays = "horizon_days"
        case minimumBuffer = "minimum_buffer"
        case startingBalance = "starting_balance"
        case minProjectedBalance = "min_projected_balance"
        case endingBalance = "ending_balance"
        case daysUntilZero = "days_until_zero"
        case daysUntilBelowBuffer = "days_until_below_buffer"
        case overdraftRisk = "overdraft_risk"
        case suggestedDailyCutback = "suggested_daily_cutback"
        case incomeAssumed = "income_assumed"
        case note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        horizonDays = try container.decode(Int.self, forKey: .horizonDays)
        minimumBuffer = try container.decodeFlexibleDecimal(forKey: .minimumBuffer)
        startingBalance = try container.decodeFlexibleDecimal(forKey: .startingBalance)
        minProjectedBalance = try container.decodeFlexibleDecimal(forKey: .minProjectedBalance)
        endingBalance = try container.decodeFlexibleDecimal(forKey: .endingBalance)
        daysUntilZero = try container.decodeIfPresent(Int.self, forKey: .daysUntilZero)
        daysUntilBelowBuffer = try container.decodeIfPresent(Int.self, forKey: .daysUntilBelowBuffer)
        overdraftRisk = try container.decode(String.self, forKey: .overdraftRisk)
        suggestedDailyCutback = try container.decodeFlexibleDecimal(forKey: .suggestedDailyCutback)
        incomeAssumed = try container.decode(Bool.self, forKey: .incomeAssumed)
        note = try container.decode(String.self, forKey: .note)
    }
}

struct ScenarioSeries: Decodable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let label: String
    let endingBalance: Decimal
    let minBalance: Decimal
    let daysUntilZero: Int?
    let overdraftRisk: String
    let estimatedSpendTotal: Decimal
    let projectedIncomeTotal: Decimal
    let safeToSpend: Decimal
    let deltaMinBalanceVsBaseline: Decimal
    let points: [CashflowDayPoint]
    let note: String

    enum CodingKeys: String, CodingKey {
        case name, label, points, note
        case endingBalance = "ending_balance"
        case minBalance = "min_balance"
        case daysUntilZero = "days_until_zero"
        case overdraftRisk = "overdraft_risk"
        case estimatedSpendTotal = "estimated_spend_total"
        case projectedIncomeTotal = "projected_income_total"
        case safeToSpend = "safe_to_spend"
        case deltaMinBalanceVsBaseline = "delta_min_balance_vs_baseline"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        label = try container.decode(String.self, forKey: .label)
        endingBalance = try container.decodeFlexibleDecimal(forKey: .endingBalance)
        minBalance = try container.decodeFlexibleDecimal(forKey: .minBalance)
        daysUntilZero = try container.decodeIfPresent(Int.self, forKey: .daysUntilZero)
        overdraftRisk = try container.decode(String.self, forKey: .overdraftRisk)
        estimatedSpendTotal = try container.decodeFlexibleDecimal(forKey: .estimatedSpendTotal)
        projectedIncomeTotal = try container.decodeFlexibleDecimal(forKey: .projectedIncomeTotal)
        safeToSpend = try container.decodeFlexibleDecimal(forKey: .safeToSpend)
        deltaMinBalanceVsBaseline = try container.decodeFlexibleDecimal(forKey: .deltaMinBalanceVsBaseline)
        points = try container.decode([CashflowDayPoint].self, forKey: .points)
        note = try container.decode(String.self, forKey: .note)
    }
}

struct ScenarioSimulationResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let horizonDays: Int
    let incomeAssumed: Bool
    let baselineMinBalance: Decimal
    let scenarios: [ScenarioSeries]
    let assumptions: [String]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case horizonDays = "horizon_days"
        case incomeAssumed = "income_assumed"
        case baselineMinBalance = "baseline_min_balance"
        case scenarios, assumptions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        horizonDays = try container.decode(Int.self, forKey: .horizonDays)
        incomeAssumed = try container.decode(Bool.self, forKey: .incomeAssumed)
        baselineMinBalance = try container.decodeFlexibleDecimal(forKey: .baselineMinBalance)
        scenarios = try container.decode([ScenarioSeries].self, forKey: .scenarios)
        assumptions = try container.decodeIfPresent([String].self, forKey: .assumptions) ?? []
    }
}

struct PaymentCycleConfig: Codable, Sendable {
    var cadence: String
    var nextPayday: String?
    var paycheckAmount: Decimal
    var minimumBuffer: Decimal
    var billDayOverrides: [String: Int]

    enum CodingKeys: String, CodingKey {
        case cadence
        case nextPayday = "next_payday"
        case paycheckAmount = "paycheck_amount"
        case minimumBuffer = "minimum_buffer"
        case billDayOverrides = "bill_day_overrides"
    }

    init(
        cadence: String = "none",
        nextPayday: String? = nil,
        paycheckAmount: Decimal = 0,
        minimumBuffer: Decimal = 250,
        billDayOverrides: [String: Int] = [:]
    ) {
        self.cadence = cadence
        self.nextPayday = nextPayday
        self.paycheckAmount = paycheckAmount
        self.minimumBuffer = minimumBuffer
        self.billDayOverrides = billDayOverrides
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        cadence = try container.decode(String.self, forKey: .cadence)
        nextPayday = try container.decodeIfPresent(String.self, forKey: .nextPayday)
        paycheckAmount = try container.decodeFlexibleDecimal(forKey: .paycheckAmount)
        minimumBuffer = try container.decodeFlexibleDecimal(forKey: .minimumBuffer)
        billDayOverrides = try container.decodeIfPresent([String: Int].self, forKey: .billDayOverrides) ?? [:]
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(cadence, forKey: .cadence)
        try container.encodeIfPresent(nextPayday, forKey: .nextPayday)
        try container.encode(NSDecimalNumber(decimal: paycheckAmount).stringValue, forKey: .paycheckAmount)
        try container.encode(NSDecimalNumber(decimal: minimumBuffer).stringValue, forKey: .minimumBuffer)
        try container.encode(billDayOverrides, forKey: .billDayOverrides)
    }
}

struct PaymentCycleSettingsResponse: Decodable, Sendable {
    let customerId: String
    let paymentCycle: PaymentCycleConfig
    let source: String

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case paymentCycle = "payment_cycle"
        case source
    }
}

struct BehaviorSegmentResponse: Decodable, Sendable {
    let customerId: String
    let asOf: String
    let segment: String
    let confidence: Decimal
    let tips: [String]
    let loanPaymentSuggestions: [String]

    enum CodingKeys: String, CodingKey {
        case customerId = "customer_id"
        case asOf = "as_of"
        case segment, confidence, tips
        case loanPaymentSuggestions = "loan_payment_suggestions"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        customerId = try container.decode(String.self, forKey: .customerId)
        asOf = try container.decode(String.self, forKey: .asOf)
        segment = try container.decode(String.self, forKey: .segment)
        confidence = try container.decodeFlexibleDecimal(forKey: .confidence)
        tips = try container.decodeIfPresent([String].self, forKey: .tips) ?? []
        loanPaymentSuggestions = try container.decodeIfPresent([String].self, forKey: .loanPaymentSuggestions) ?? []
    }
}
