"""
routes_ai.py — V2 AI assistant + smart search (internal users only).

  GET  /api/ai/status                    {configured, installed, model}
  GET  /api/ai/conversations             the signed-in user's own conversations
  GET  /api/ai/conversations/<id>        one of them, with its messages
  POST /api/ai/ask                       {conversationId?, message, alarmId?, projectId?, tzOffset?}
  GET  /api/search/vocabulary            what plain-language search can recognise (scope-filtered)
  POST /api/search/interpret             {text, tzOffset?} → filters (Claude, validated against the vocabulary)

Every data access goes through ai.py's tools, which call the same server functions as the screens with the
same user: RBAC, audience and scope apply unchanged. Client users get 404 (audience wall).
"""

from flask import Blueprint, jsonify

import ai
import rbac
from camview_client import ApiError
from routes_common import body

bp = Blueprint("ai", __name__)


def _user():
    return rbac.current_user()


def _tz_offset(b):
    try:
        return max(-900, min(900, int(b.get("tzOffset") or 0)))
    except (TypeError, ValueError):
        return 0


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ai.AIError as e:
        raise ApiError(e.code, e.message, e.status) from None


@bp.route("/api/ai/status")
@rbac.internal("alarm.view")
def ai_status():
    return jsonify(ai.status())


@bp.route("/api/ai/conversations")
@rbac.internal("alarm.view")
def ai_conversations():
    return jsonify({"items": ai.list_conversations(_user())})


@bp.route("/api/ai/conversations/<conv_id>")
@rbac.internal("alarm.view")
def ai_conversation(conv_id):
    c = ai.get_conversation(_user(), conv_id)
    if not c:
        raise ApiError("not_found", "Conversation not available.", 404)
    return jsonify(c)


@bp.route("/api/ai/ask", methods=["POST"])
@rbac.internal("alarm.view")
def ai_ask():
    b = body()
    return jsonify(_call(ai.ask, _user(), b.get("message"), conversation_id=b.get("conversationId") or None,
                         alarm_id=(str(b["alarmId"]).strip() if b.get("alarmId") else None),
                         project_id=(str(b["projectId"]).strip() if b.get("projectId") not in (None, "") else None),
                         tz_offset=_tz_offset(b)))


@bp.route("/api/search/vocabulary")
@rbac.internal("alarm.view")
def search_vocabulary():
    out = ai.vocabulary(_user())
    out["ai"] = ai.configured() and ai.installed()
    return jsonify(out)


@bp.route("/api/search/interpret", methods=["POST"])
@rbac.internal("alarm.view")
def search_interpret():
    b = body()
    return jsonify(_call(ai.interpret, _user(), b.get("text"), tz_offset=_tz_offset(b)))
