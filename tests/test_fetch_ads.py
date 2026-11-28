"""fetch_ads: report parsing, Pacific day boundaries and report windows (no eBay or database calls)."""
from datetime import date
from decimal import Decimal

from traffic_analytics.fetch_ads import money, pacific_midnight, parse, report_day, windows

HEAD = "listing_id\tlisting_title\tcampaign_id\tad_group_id\tday\tcpc_impressions\tcpc_clicks\tcpc_ad_fees_listingsite_currency\tcpc_sale_amount_listingsite_currency\tcpc_attributed_sales"
TSV = "\n".join([
    HEAD,
    "287171146041\tPokemon Lot\t159727416018\t1\tNov 17, 2026\t7000\t58\tUSD 10.39\tUSD 0.00\t0",
    "287171146041\tPokemon Lot\t159727416018\t2\tNov 17, 2026\t100\t1\tUSD 0.27\tUSD 0.00\t0",
    "287171146041\tPokemon Lot\t159727416018\t1\tNov 18, 2026\t9873\t73\tUSD 12.62\tUSD 10.72\t1",
    "287171146041\tPokemon Lot\t159727416018\t1\tNov 19, 2026\t500\t2\tUSD 0.40\tUSD 0.00\t0",
    "",
])


def test_ad_groups_are_added_per_listing_campaign_and_day():
    rows = parse(TSV, date(2026, 11, 17), date(2026, 11, 18))
    day17 = rows[(date(2026, 11, 17), "287171146041", "159727416018")]
    assert (day17["imp"], day17["clicks"], day17["fees"], day17["sold"]) == (7100, 59, Decimal("10.66"), 0)
    day18 = rows[(date(2026, 11, 18), "287171146041", "159727416018")]
    assert (day18["fees"], day18["sales"], day18["sold"]) == (Decimal("12.62"), Decimal("10.72"), 1)


def test_days_outside_the_window_are_dropped():
    # eBay also sends part of today (seen 2026-11-24): only the asked days are kept.
    rows = parse(TSV, date(2026, 11, 17), date(2026, 11, 18))
    assert {k[0] for k in rows} == {date(2026, 11, 17), date(2026, 11, 18)}


def test_report_days_are_calendar_days():
    assert report_day("Nov 17, 2026") == date(2026, 11, 17)
    assert report_day("Jan 3, 2027") == date(2027, 1, 3)
    assert report_day("17/11/2026") is None
    assert money("USD 1,234.50") == Decimal("1234.50")
    assert money("") == Decimal("0")


def test_pacific_midnight_follows_the_clock_change():
    assert pacific_midnight(date(2026, 10, 31)) == "2026-10-31T07:00:00.000Z"  # daylight time
    assert pacific_midnight(date(2026, 11, 2)) == "2026-11-02T08:00:00.000Z"  # standard time


def test_windows_are_at_most_seven_days():
    # eBay refuses a by-day report longer than 7 days.
    assert list(windows(date(2026, 10, 4), date(2026, 10, 19))) == [
        (date(2026, 10, 4), date(2026, 10, 10)),
        (date(2026, 10, 11), date(2026, 10, 17)),
        (date(2026, 10, 18), date(2026, 10, 19)),
    ]


def test_the_clock_change_week_is_one_day_shorter():
    # Nov 1, 2026 the clocks go back: Nov 1-7 is 169 hours, which eBay refuses.
    assert list(windows(date(2026, 11, 1), date(2026, 11, 14))) == [
        (date(2026, 11, 1), date(2026, 11, 6)),
        (date(2026, 11, 7), date(2026, 11, 13)),
        (date(2026, 11, 14), date(2026, 11, 14)),
    ]
