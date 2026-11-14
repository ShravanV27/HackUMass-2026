import Foundation

enum ChartDate {
    static let axisFormat = Date.FormatStyle()
        .month(.abbreviated)
        .day()

    static func parse(_ raw: String) -> Date? {
        let isoDay = Date.ISO8601FormatStyle().year().month().day()
        if let date = try? Date(raw, strategy: isoDay) {
            return date
        }
        return try? Date(raw, strategy: Date.ISO8601FormatStyle())
    }
}
