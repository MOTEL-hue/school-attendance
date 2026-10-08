"""זמני היום, תאריך עברי, חגים, ושעון קיץ/חורף (כל החישובים לפי שעון ישראל)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pyluach import dates, parshios
from zmanim.util.geo_location import GeoLocation
from zmanim.zmanim_calendar import ZmanimCalendar

IL = ZoneInfo("Asia/Jerusalem")

# מפתח: (שם בעברית, קו רוחב, קו אורך). גובה 0 (לפי המקובל).
CITIES = {
    "jerusalem": ("ירושלים", 31.7683, 35.2137), "bnei_brak": ("בני ברק", 32.0834, 34.8344),
    "tel_aviv": ("תל אביב", 32.0853, 34.7818), "haifa": ("חיפה", 32.7940, 34.9896),
    "beit_shemesh": ("בית שמש", 31.7469, 34.9881), "ashdod": ("אשדוד", 31.8040, 34.6553),
    "elad": ("אלעד", 32.0522, 34.9510), "modiin_illit": ("מודיעין עילית", 31.9419, 35.0430),
    "beitar_illit": ("ביתר עילית", 31.6942, 35.1143), "rishon": ("ראשון לציון", 31.9730, 34.7925),
    "petah_tikva": ("פתח תקווה", 32.0840, 34.8878), "netanya": ("נתניה", 32.3215, 34.8532),
    "beer_sheva": ("באר שבע", 31.2530, 34.7915), "safed": ("צפת", 32.9646, 35.4960),
    "tiberias": ("טבריה", 32.7959, 35.5300), "ashkelon": ("אשקלון", 31.6688, 34.5743),
    "rehovot": ("רחובות", 31.8928, 34.8113), "hadera": ("חדרה", 32.4340, 34.9196),
    "afula": ("עפולה", 32.6078, 35.2897), "netivot": ("נתיבות", 31.4226, 34.5951),
    "ofakim": ("אופקים", 31.3122, 34.6217), "kiryat_gat": ("קרית גת", 31.6100, 34.7642),
    "lod": ("לוד", 31.9515, 34.8881), "ramla": ("רמלה", 31.9279, 34.8725), "karmiel": ("כרמיאל", 32.9190, 35.2950),
    "nahariya": ("נהריה", 33.0050, 35.0950), "acre": ("עכו", 32.9281, 35.0818), "eilat": ("אילת", 29.5577, 34.9519),
    "maale_adumim": ("מעלה אדומים", 31.7773, 35.2986), "immanuel": ("עמנואל", 32.1646, 35.1500),
    "kiryat_arba": ("קרית ארבע", 31.5333, 35.1000), "ariel": ("אריאל", 32.1043, 35.1696),
    "rosh_haayin": ("ראש העין", 32.0956, 34.9566), "ramat_gan": ("רמת גן", 32.0684, 34.8248),
    "bat_yam": ("בת ים", 32.0132, 34.7510), "holon": ("חולון", 32.0158, 34.7795),
    "kiryat_malachi": ("קרית מלאכי", 31.7294, 34.7442), "yavne": ("יבנה", 31.8776, 34.7396),
    "dimona": ("דימונה", 31.0700, 35.0333), "arad": ("ערד", 31.2589, 35.2128), "sderot": ("שדרות", 31.5250, 34.5961),
    "migdal_haemek": ("מגדל העמק", 32.6773, 35.2400), "kiryat_shmona": ("קרית שמונה", 33.2078, 35.5703),
    "custom": ("מותאם אישית (קואורדינטות)", None, None),
}
CITY_CHOICES = [(k, v[0]) for k, v in CITIES.items()]
CANDLE_CHOICES = [(18, "18 דקות (ירושלים: 40)"), (20, "20 דקות"), (22, "22 דקות"), (30, "30 דקות"), (40, "40 דקות")]
HEB_MONTHS = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
DAY_NAMES_HE = {6: "ראשון", 0: "שני", 1: "שלישי", 2: "רביעי", 3: "חמישי", 4: "שישי", 5: "שבת"}

# חגים שבהם בדרך כלל אין לימודים (ברירת מחדל מסומנת להוספה אוטומטית)
OFF_DEFAULT = {"ראש השנה", "יום כיפור", "סוכות", "שמיני עצרת", "פסח", "שבועות"}
OFF_OPTIONAL = {"חנוכה", "פורים", "שושן פורים", "ל״ג בעומר", "תענית אסתר", "צום גדליה", "י׳ בטבת", "י״ז בתמוז", "ט׳ באב"}
YOM_TOV_NAMES = {"ראש השנה", "יום כיפור", "סוכות", "שמיני עצרת", "פסח", "שבועות"}


def _geo(school):
    name, lat, lon = CITIES.get(school.city_key, CITIES["jerusalem"])
    if school.city_key == "custom" and school.custom_lat is not None and school.custom_lon is not None:
        lat, lon = school.custom_lat, school.custom_lon
    elif lat is None:
        _, lat, lon = CITIES["jerusalem"]
    return GeoLocation(name, lat, lon, "Asia/Jerusalem", 0)


def _heb(d: date):
    return dates.GregorianDate(d.year, d.month, d.day).to_heb()


def hebrew_day_letters(d: date) -> str:
    return _heb(d).hebrew_day()


def festival(d: date):
    """שם החג (לפי לוח ישראל), תענית, או ריק."""
    h = _heb(d)
    return h.festival(israel=True, hebrew=True, include_working_days=True) or h.fast_day(hebrew=True) or ""


def is_yom_tov(d: date) -> bool:
    """יום טוב שבו אסור במלאכה (לא חול המועד)."""
    return bool(_heb(d).festival(israel=True, hebrew=True, include_working_days=False)) and \
        _heb(d).festival(israel=True, hebrew=True, include_working_days=False) in YOM_TOV_NAMES


def rosh_chodesh(d: date) -> bool:
    h = _heb(d)
    day = int(h.tuple()[2]) if hasattr(h, "tuple") else 0
    return (day == 1 and h.tuple()[1] != 7) or day == 30


def day_label(d: date) -> dict:
    h = _heb(d)
    parsha = ""
    if d.weekday() == 5:
        parsha = parshios.getparsha_string(dates.GregorianDate(d.year, d.month, d.day), israel=True, hebrew=True) or ""
    return {"hebrew": h.hebrew_date_string(), "hday": h.hebrew_day(), "festival": festival(d),
            "rosh_chodesh": rosh_chodesh(d), "parsha": parsha, "weekday": DAY_NAMES_HE[d.weekday()]}


def _loc(dt):
    return dt.astimezone(IL) if dt else None


def zmanim_for(school, d: date) -> dict:
    """זמני היום ליום אחד. הערכים הם datetime עם אזור זמן ישראל (או None)."""
    c = ZmanimCalendar(geo_location=_geo(school), date=d)
    z = {
        "alos": _loc(c.alos_72()), "hanetz": _loc(c.hanetz()),
        "shma_mga": _loc(c.sof_zman_shma_mga()), "shma_gra": _loc(c.sof_zman_shma_gra()),
        "tefila_mga": _loc(c.sof_zman_tfila_mga()), "tefila_gra": _loc(c.sof_zman_tfila_gra()),
        "chatzos": _loc(c.chatzos()), "mincha_gedola": _loc(c.mincha_gedola()),
        "mincha_ketana": _loc(c.mincha_ketana()), "plag": _loc(c.plag_hamincha()),
        "shkia": _loc(c.shkia()), "tzais": _loc(c.tzais()), "candle": None, "havdalah": None,
    }
    tomorrow_yt = is_yom_tov(d + timedelta(days=1))
    today_off = d.weekday() == 5 or is_yom_tov(d)
    if (d.weekday() == 4 or tomorrow_yt) and z["shkia"]:
        z["candle"] = z["shkia"] - timedelta(minutes=school.candle_minutes)
    if today_off and not (d.weekday() == 4) and not tomorrow_yt and z["tzais"]:
        z["havdalah"] = z["tzais"]
    return z


ZMAN_ROWS = [
    ("alos", "עלות השחר (72 דקות)"), ("hanetz", "הנץ החמה"), ("shma_mga", "סוף זמן קריאת שמע (מג״א)"),
    ("shma_gra", "סוף זמן קריאת שמע (גר״א)"), ("tefila_mga", "סוף זמן תפילה (מג״א)"),
    ("tefila_gra", "סוף זמן תפילה (גר״א)"), ("chatzos", "חצות היום"), ("mincha_gedola", "מנחה גדולה"),
    ("mincha_ketana", "מנחה קטנה"), ("plag", "פלג המנחה"), ("shkia", "שקיעה"), ("tzais", "צאת הכוכבים"),
    ("candle", "הדלקת נרות"), ("havdalah", "הבדלה / צאת שבת וחג"),
]


# ---------- שעון קיץ / חורף ----------
def dst_active(d: date) -> bool:
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=IL)
    return bool(noon.dst())


def dst_changes(year: int):
    """תאריכי מעבר שעון בשנה: [(תאריך, 'summer'|'winter')]."""
    out, prev = [], None
    d = date(year, 1, 1)
    while d.year == year:
        cur = dst_active(d)
        if prev is not None and cur != prev:
            out.append((d, "summer" if cur else "winter"))
        prev, d = cur, d + timedelta(days=1)
    return out


def season_for(school, d: date) -> str:
    mode = getattr(school, "season_mode", "auto")
    if mode in ("winter", "summer"):
        return mode
    return "summer" if dst_active(d) else "winter"


def season_label(season: str) -> str:
    return "שעון קיץ" if season == "summer" else "שעון חורף"


def school_hours(school, d: date):
    """(התחלה, סיום) ליום לימודים, לפי עונה ויום בשבוע; None אם לא הוגדר."""
    if d.weekday() == 4:
        return (school.start_winter if season_for(school, d) == "winter" else school.start_summer), school.friday_end
    if season_for(school, d) == "summer":
        return school.start_summer, school.end_summer
    return school.start_winter, school.end_winter
