import SwiftUI
import WidgetKit

struct AIQuotaBarWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    let entry: QuotaEntry

    var body: some View {
        switch family {
        case .systemSmall:
            SmallWidgetView(entry: entry)
        default:
            MediumWidgetView(entry: entry)
        }
    }
}

struct AIQuotaBarWidget: Widget {
    let kind = "AIQuotaBarWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: QuotaProvider()) { entry in
            AIQuotaBarWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("AI Quota")
        .description("Monitor Claude and ChatGPT usage limits.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct AIQuotaBarWidgetBundle: WidgetBundle {
    var body: some Widget {
        AIQuotaBarWidget()
    }
}
