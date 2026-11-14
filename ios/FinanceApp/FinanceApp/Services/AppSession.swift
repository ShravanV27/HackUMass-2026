import Foundation
import Observation

/// Client preferences + onboarding. Server holds live bank binding.
@Observable
final class AppSession {
    private static let onboardedKey = "pulseguard.onboarded"
    private static let nessieIdKey = "pulseguard.bound_bank_id"
    private static let bufferKey = "pulseguard.minimum_buffer"
    private static let horizonKey = "pulseguard.horizon_days"
    private static let developerKey = "pulseguard.developer_details"

    var hasCompletedOnboarding: Bool {
        didSet { UserDefaults.standard.set(hasCompletedOnboarding, forKey: Self.onboardedKey) }
    }

    var boundNessieCustomerId: String {
        didSet { UserDefaults.standard.set(boundNessieCustomerId, forKey: Self.nessieIdKey) }
    }

    /// Safety buffer used across Home / What If? / Settings.
    var minimumBuffer: Decimal {
        didSet {
            UserDefaults.standard.set(
                NSDecimalNumber(decimal: minimumBuffer).stringValue,
                forKey: Self.bufferKey
            )
        }
    }

    var forecastHorizonDays: Int {
        didSet { UserDefaults.standard.set(forecastHorizonDays, forKey: Self.horizonKey) }
    }

    var showDeveloperDetails: Bool {
        didSet { UserDefaults.standard.set(showDeveloperDetails, forKey: Self.developerKey) }
    }

    init() {
        // Migrate legacy keys if present.
        if UserDefaults.standard.object(forKey: Self.onboardedKey) == nil,
           UserDefaults.standard.bool(forKey: "financeapp.onboarded") {
            UserDefaults.standard.set(true, forKey: Self.onboardedKey)
        }
        hasCompletedOnboarding = UserDefaults.standard.bool(forKey: Self.onboardedKey)
        boundNessieCustomerId =
            UserDefaults.standard.string(forKey: Self.nessieIdKey)
            ?? UserDefaults.standard.string(forKey: "financeapp.bound_nessie_id")
            ?? ""
        if let raw = UserDefaults.standard.string(forKey: Self.bufferKey),
           let value = Decimal(string: raw) {
            minimumBuffer = value
        } else {
            minimumBuffer = 250
        }
        let storedHorizon = UserDefaults.standard.integer(forKey: Self.horizonKey)
        forecastHorizonDays = [7, 14, 30].contains(storedHorizon) ? storedHorizon : 14
        showDeveloperDetails = UserDefaults.standard.bool(forKey: Self.developerKey)
    }

    func completeOnboarding(nessieCustomerId: String?) {
        if let nessieCustomerId, !nessieCustomerId.isEmpty {
            boundNessieCustomerId = nessieCustomerId
        }
        hasCompletedOnboarding = true
    }
}

extension Notification.Name {
    /// Posted after Demo Mode writes or settings changes so tabs refresh.
    static let pulseDataDidChange = Notification.Name("pulseGuard.dataDidChange")
}
