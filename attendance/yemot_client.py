"""קריאות ל-API הניהולי של ימות המשיח (call2all). רק urllib, בלי תלות נוספת."""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.call2all.co.il/ym/api/"


class YemotError(Exception):
    pass


def _call(command, token, params, json_body=None):
    if not token:
        raise YemotError("לא הוגדרו מספר מערכת וסיסמה של ימות המשיח")
    q = urllib.parse.urlencode({"token": token, **params})
    req = urllib.request.Request(f"{BASE}{command}?{q}")
    if json_body is not None:
        req = urllib.request.Request(f"{BASE}{command}?{q}", data=json.dumps(json_body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        raise YemotError(f"תקלה בתקשורת עם ימות המשיח: {e}") from e
    if data.get("responseStatus") not in ("OK", None):
        raise YemotError(data.get("message") or data.get("responseStatus"))
    return data


def run_tzintuk(settings, phones):
    params = {"phones": ":".join(phones), "TzintukTimeOut": "9"}
    if settings.caller_id:
        params["callerId"] = settings.caller_id
    _call("RunTzintuk", settings.yemot_token, params)
    return "צינתוק"


def run_voice(settings, phones, text):
    """קמפיין קולי עם הקראת טקסט אישי (דורש תבנית קמפיין בימות המשיח)."""
    if not settings.voice_template_id:
        raise YemotError("לא הוגדר מזהה תבנית קמפיין להודעה קולית")
    params = {"templateId": settings.voice_template_id, "ttsMode": "1",
              "phones": json.dumps({p: {"text": text} for p in phones}, ensure_ascii=False)}
    if settings.caller_id:
        params["callerId"] = settings.caller_id
    _call("RunCampaign", settings.yemot_token, params)
    return "שיחה קולית"


def update_extension(settings, path, values):
    """יצירה/עדכון שלוחה. לא מוחק הגדרות קיימות שלא נשלחו."""
    _call("UpdateExtension", settings.yemot_token, {"path": f"ivr2:{path}", **values})
