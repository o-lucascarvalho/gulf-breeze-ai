from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from enum import Enum
from typing import Any

from app.agents.context import ConversationContext, ConversationMessage, NoteReference
from app.agents.exceptions import AgentMaxRoundsExceeded
from app.agents.prompts import SYSTEM_PROMPT
from app.agents.types import AgentModel, AgentResult, AgentToolCall
from app.services.agent_actions import AgentActionService
from app.services.agent_sessions import AgentSessionService
from app.tools import build_default_tool_registry
from app.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from embedding.openai_provider import OpenAIEmbeddingProvider  # noqa: E402
from retrieval.service import PgVectorSemanticRetriever  # noqa: E402

from app.agents.models import OpenAIToolCallingModel  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


class EvidenceStatus(str, Enum):
    EXPLICIT_CONFIRMATION = "explicit_confirmation"
    EXPLICIT_NEGATION = "explicit_negation"
    POSSIBLE_OR_SUSPECTED = "possible_or_suspected"
    RELATED_BUT_NOT_CONFIRMING = "related_but_not_confirming"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


_EVIDENCE_CONFIRMATION_PATTERNS = (
    r"\bdetected\b",
    r"\bconfirmed\b",
    r"\bfound\b",
    r"\bdiagnosed\b",
    r"\bdocumented\b",
    r"\bverified\b",
    r"\bidentified\b",
    r"\breplaced\b",
    r"\binstalled\b",
    r"\bleak\s+detected\b",
    r"\bleak\s+found\b",
    r"\bleak\s+at\b",
    r"\bleaking\b",
)

_EVIDENCE_NEGATION_PATTERNS = (
    r"\bcould not find\b",
    r"\bcouldn't find\b",
    r"\bnot found\b",
    r"\bno leak\b",
    r"\bno evidence of\b",
    r"\bcould not locate\b",
    r"\bunable to locate\b",
    r"\bdid not find\b",
    r"\bno sign of\b",
)

_EVIDENCE_POSSIBLE_PATTERNS = (
    r"\bmay be\b",
    r"\bpossible\b",
    r"\bsuspected\b",
    r"\bcould be\b",
    r"\bmight be\b",
    r"\bthinks?\b",
    r"\bappears\b",
    r"\bseems\b",
    r"\blikely\b",
    r"\blow on refrigerant\b",
    r"\babout \d+ lbs low\b",
)

_EVIDENCE_RELATED_PATTERNS = (
    r"\brefrigerant\b",
    r"\bleak\b",
    r"\bcompressor\b",
    r"\bcapacitor\b",
    r"\bvalve\b",
    r"\bcoil\b",
    r"\bboard\b",
    r"\brepair\b",
    r"\brecover\b",
    r"\bevacuat",
    r"\bquote\b",
    r"\bestimate\b",
    r"\bcooling\b",
    r"\befficiency\b",
)

_OVERCLAIM_PATTERNS = (
    r"^\s*(yes|yep|yeah|absolutely|certainly)\b",
    r"\bconfirmed\b",
    r"\bconfirm(?:ed|s)?\b",
    r"\bprevious (?:refrigerant )?leaks?\b",
    r"\bconfirmed (?:refrigerant )?leak\b",
    r"\bhad (?:a |some )?(?:confirmed )?(?:refrigerant )?leak\b",
    r"\bthere (?:was|were) (?:a |some )?(?:confirmed )?(?:refrigerant )?leak\b",
    r"\bfound (?:a )?(?:refrigerant )?leak\b",
    r"\bwas (?:a )?(?:confirmed )?(?:refrigerant )?leak\b",
    r"\bcaused\b",
    r"\bled to\b",
    r"\bfailed\b",
)

_NON_OVERCLAIM_PATTERNS = (
    r"\bdo not confirm\b",
    r"\bdoes not confirm\b",
    r"\bdid not confirm\b",
    r"\bnot confirmed\b",
    r"\bno leak\b",
    r"\bnot a leak\b",
    r"\bnot establish\b",
    r"\bcould not find\b",
    r"\bcould not locate\b",
)


class AgentOrchestrator:
    def __init__(
        self,
        *,
        model: AgentModel,
        registry: ToolRegistry,
        session_service: AgentSessionService,
        action_service: AgentActionService,
        max_tool_rounds: int,
        max_history_messages: int,
    ) -> None:
        self._model = model
        self._registry = registry
        self._session_service = session_service
        self._action_service = action_service
        self._max_tool_rounds = max_tool_rounds
        self._max_history_messages = max_history_messages

    @property
    def session_service(self) -> AgentSessionService:
        return self._session_service

    @property
    def max_tool_rounds(self) -> int:
        return self._max_tool_rounds

    def run(
        self,
        user_message: str,
        *,
        session_id: int | None = None,
        channel: str = "cli",
        caller_identifier: str | None = None,
        finalize_session_on_return: bool = True,
    ) -> AgentResult:
        if session_id is None:
            session_id = self._session_service.create_session(
                channel=channel,
                caller_identifier=caller_identifier,
                metadata={"tool_round_limit": self._max_tool_rounds},
            )
            context = ConversationContext()
        else:
            record = self._session_service.get_session(session_id)
            context = ConversationContext.from_metadata(record.metadata_json if record else None)

        resolved_note_ref = context.resolve_note_reference(user_message)
        enriched_user_message = self._enrich_user_message_with_context(user_message, context)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": self._build_context_system_message(context),
            },
        ]
        messages.extend(self._context_messages_for_model(context))
        messages.append({"role": "user", "content": enriched_user_message})

        tool_calls: list[AgentToolCall] = []
        needs_handoff = False
        handoff_reason: str | None = None
        turn_messages: list[ConversationMessage] = [ConversationMessage(role="user", content=user_message)]

        try:
            for _ in range(self._max_tool_rounds):
                model_response = self._model.run(messages=messages, tools=self._registry.list_tool_specs())
                assistant_content = model_response.message or ""

                if model_response.tool_calls:
                    assistant_tool_calls = []
                    for call in model_response.tool_calls:
                        assistant_tool_calls.append(
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                        )

                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_content,
                            "tool_calls": assistant_tool_calls,
                        }
                    )
                    turn_messages.append(
                        ConversationMessage(role="assistant", content=assistant_content)
                    )

                    for call in model_response.tool_calls:
                        tool_name, arguments = self._resolve_effective_tool_call(
                            call.name,
                            dict(call.arguments),
                            context,
                            resolved_note_ref,
                        )
                        if tool_name == "reschedule_appointment":
                            if not arguments.get("agent_session_id"):
                                arguments["agent_session_id"] = session_id
                            if not arguments.get("idempotency_key"):
                                arguments["idempotency_key"] = f"{session_id}:{call.call_id}"
                        execution = self._registry.execute(tool_name, arguments)
                        payload = execution.payload
                        record = execution.record

                        entity_type, entity_id = self._derive_entity(tool_name, arguments, payload)
                        self._action_service.log_tool_call(
                            session_id=session_id,
                            action_type=tool_name,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            arguments=arguments,
                            result=payload,
                            status=record.status,
                            latency_ms=record.latency_ms,
                        )

                        tool_calls.append(
                            AgentToolCall(
                                tool_name=tool_name,
                                arguments=arguments,
                                status=record.status,
                                latency_ms=record.latency_ms,
                                error=record.error.__dict__ if record.error else None,
                            )
                        )

                        self._apply_tool_result_to_context(context, tool_name, arguments, payload)

                        if tool_name == "request_handoff" and payload.get("ok"):
                            data = payload.get("data", {})
                            needs_handoff = bool(data.get("needs_handoff"))
                            handoff_reason = data.get("handoff_reason")

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.call_id,
                                "content": json.dumps(payload),
                            }
                        )
                    continue

                response_text = assistant_content.strip()
                if not response_text:
                    response_text = (
                        "I could not produce a grounded answer from available Gulf Breeze Air records. "
                        "Please contact the office for assistance."
                    )
                    needs_handoff = True
                    handoff_reason = handoff_reason or "No grounded answer generated"

                response_text = self._apply_grounding_safeguards(response_text, context, tool_calls)

                turn_messages.append(ConversationMessage(role="assistant", content=response_text))
                context.upsert_messages(turn_messages, max_history_messages=self._max_history_messages)

                if needs_handoff:
                    final_status = "handoff"
                    mark_ended = True
                elif finalize_session_on_return:
                    final_status = "completed"
                    mark_ended = True
                else:
                    final_status = "active"
                    mark_ended = False

                self._session_service.update_session(
                    session_id=session_id,
                    status=final_status,
                    customer_id=context.customer_id,
                    metadata_updates={
                        "needs_handoff": needs_handoff,
                        "handoff_reason": handoff_reason,
                        "conversation_context": context.to_metadata_payload(),
                    },
                    mark_ended=mark_ended,
                )
                return AgentResult(
                    response=response_text,
                    session_id=session_id,
                    status=final_status,
                    needs_handoff=needs_handoff,
                    handoff_reason=handoff_reason,
                    tool_calls=tool_calls,
                    active_customer_id=context.customer_id,
                    active_job_id=context.last_job_id,
                    active_invoice_id=context.last_invoice_id,
                    referenced_note_ids=[row.note_id for row in context.recent_note_references],
                )

            raise AgentMaxRoundsExceeded("Maximum tool-call rounds exceeded")
        except AgentMaxRoundsExceeded:
            handoff_reason = "Maximum tool-call rounds exceeded"
            context.upsert_messages(turn_messages, max_history_messages=self._max_history_messages)
            self._session_service.update_session(
                session_id=session_id,
                status="handoff",
                customer_id=context.customer_id,
                metadata_updates={
                    "needs_handoff": True,
                    "handoff_reason": handoff_reason,
                    "conversation_context": context.to_metadata_payload(),
                },
                mark_ended=True,
            )
            return AgentResult(
                response=(
                    "I need office assistance to complete this request because the tool workflow exceeded its safe limit."
                ),
                session_id=session_id,
                status="handoff",
                needs_handoff=True,
                handoff_reason=handoff_reason,
                tool_calls=tool_calls,
                active_customer_id=context.customer_id,
                active_job_id=context.last_job_id,
                active_invoice_id=context.last_invoice_id,
                referenced_note_ids=[row.note_id for row in context.recent_note_references],
            )
        except Exception as exc:  # pragma: no cover
            context.upsert_messages(turn_messages, max_history_messages=self._max_history_messages)
            self._session_service.update_session(
                session_id=session_id,
                status="failed",
                customer_id=context.customer_id,
                metadata_updates={
                    "error": str(exc),
                    "conversation_context": context.to_metadata_payload(),
                },
                mark_ended=True,
            )
            return AgentResult(
                response=(
                    "I ran into an internal issue while checking Gulf Breeze Air records. "
                    "Please contact the office for assistance."
                ),
                session_id=session_id,
                status="failed",
                needs_handoff=True,
                handoff_reason="internal_error",
                tool_calls=tool_calls,
                active_customer_id=context.customer_id,
                active_job_id=context.last_job_id,
                active_invoice_id=context.last_invoice_id,
                referenced_note_ids=[row.note_id for row in context.recent_note_references],
            )

    @staticmethod
    def _derive_customer_display_name(match: dict[str, Any]) -> str | None:
        full_name = " ".join(
            [part for part in [match.get("first_name"), match.get("last_name")] if part]
        ).strip()
        return full_name or match.get("company")

    def _apply_tool_result_to_context(
        self,
        context: ConversationContext,
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if not payload.get("ok"):
            return

        data = payload.get("data", {})

        if tool_name == "search_customers":
            matches = data.get("matches") or []
            if len(matches) == 1:
                customer_id = matches[0].get("customer_id")
                display_name = self._derive_customer_display_name(matches[0])
                context.set_customer(customer_id, display_name)
            elif data.get("ambiguous"):
                context.set_customer(None, None)
                context.clear_customer_scoped_references()
            return

        if tool_name == "get_customer":
            customer = data.get("customer") or {}
            if customer.get("customer_id"):
                display_name = " ".join(
                    [part for part in [customer.get("first_name"), customer.get("last_name")] if part]
                ).strip() or customer.get("company")
                context.set_customer(customer.get("customer_id"), display_name)
            return

        if tool_name in {"get_customer_jobs", "get_customer_balance", "get_upcoming_appointments"}:
            customer_id = data.get("customer_id") or arguments.get("customer_id")
            if customer_id:
                context.set_customer(customer_id, context.customer_display_name)

        if tool_name == "get_upcoming_appointments":
            appointments = data.get("appointments") or []
            context.set_recent_job_references(appointments, customer_id=data.get("customer_id"))
            return

        if tool_name == "get_customer_jobs":
            jobs = data.get("jobs") or []
            context.set_recent_job_references(jobs, customer_id=data.get("customer_id"))
            return

        if tool_name == "get_job":
            job = data.get("job") or {}
            if data.get("found") and job.get("job_id"):
                context.last_job_id = job.get("job_id")
                customer = job.get("customer") or {}
                if customer.get("customer_id"):
                    display_name = " ".join(
                        [part for part in [customer.get("first_name"), customer.get("last_name")] if part]
                    ).strip() or customer.get("company")
                    context.set_customer(customer.get("customer_id"), display_name)
            return

        if tool_name == "reschedule_appointment":
            if payload.get("ok"):
                job_id = data.get("job_id") or arguments.get("job_id")
                if job_id:
                    context.last_job_id = job_id
            return

        if tool_name == "get_weather_forecast":
            if payload.get("ok"):
                job_id = data.get("job_id") or arguments.get("job_id")
                if job_id:
                    context.last_job_id = job_id
            return

        if tool_name == "get_job_invoice":
            if data.get("found"):
                context.last_job_id = arguments.get("job_id") or context.last_job_id
                invoice = data.get("invoice") or {}
                context.last_invoice_id = invoice.get("invoice_id")
            return

        if tool_name == "get_note":
            note_id = data.get("note_id")
            job_id = data.get("job_id")
            customer_id = data.get("customer_id")
            content = data.get("content") or ""
            if note_id and job_id:
                existing = {row.note_id: row for row in context.recent_note_references}
                if note_id not in existing:
                    context.set_recent_note_references(
                        [
                            {
                                "note_id": note_id,
                                "job_id": job_id,
                                "customer_id": customer_id,
                                "content": content,
                                "similarity_score": None,
                            }
                        ]
                    )
                context.last_job_id = job_id
            if customer_id:
                context.set_customer(customer_id, context.customer_display_name)
            return

        if tool_name == "semantic_search_notes":
            rows = data.get("results") or []
            context.set_recent_note_references(rows)
            arg_customer = arguments.get("customer_id")
            if arg_customer:
                context.set_customer(arg_customer, context.customer_display_name)
            return

    @staticmethod
    def _derive_entity(tool_name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> tuple[str | None, str | None]:
        if tool_name == "search_customers":
            return "customer", None
        if tool_name in {"get_customer", "get_customer_jobs", "get_customer_balance", "get_upcoming_appointments"}:
            return "customer", arguments.get("customer_id")
        if tool_name in {"get_job", "get_job_invoice"}:
            return "job", arguments.get("job_id")
        if tool_name == "get_weather_forecast":
            return "job", arguments.get("job_id")
        if tool_name == "semantic_search_notes":
            return "note", arguments.get("job_id")
        if tool_name == "reschedule_appointment":
            return "job", arguments.get("job_id")
        if tool_name == "request_handoff":
            return "handoff", None
        if not payload.get("ok"):
            return "tool", None
        return None, None

    def _context_messages_for_model(self, context: ConversationContext) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in context.messages[-self._max_history_messages :]:
            payload = {"role": row.role, "content": row.content}
            if row.role == "tool" and row.tool_call_id:
                payload["tool_call_id"] = row.tool_call_id
            items.append(payload)
        return items

    @staticmethod
    def _build_context_system_message(context: ConversationContext) -> str:
        parts = ["Active short-term session context:"]
        if context.customer_id:
            parts.append(
                f"- active_customer_id={context.customer_id}"
                + (f" ({context.customer_display_name})" if context.customer_display_name else "")
            )
        if context.last_job_id:
            parts.append(f"- last_job_id={context.last_job_id}")
        if context.last_invoice_id:
            parts.append(f"- last_invoice_id={context.last_invoice_id}")
        if context.recent_note_references:
            parts.append("- recent_referenced_notes:")
            for row in context.recent_note_references:
                parts.append(f"  note {row.position}: score={row.similarity_score}, preview={row.content_preview}")
        if context.recent_job_references:
            parts.append("- recent_job_references:")
            for row in context.recent_job_references:
                parts.append(
                    f"  {row.position}. job_id={row.job_id} customer_id={row.customer_id} description={row.description}"
                )
        return "\n".join(parts)

    def _enrich_user_message_with_context(self, user_message: str, context: ConversationContext) -> str:
        hints: list[str] = []
        lower = user_message.lower()

        note_ref = context.resolve_note_reference(user_message)
        if note_ref is not None:
            hints.append(
                "Resolved note reference: "
                f"position={note_ref.position}, note_id={note_ref.note_id}, job_id={note_ref.job_id}, customer_id={note_ref.customer_id}."
            )

        if re.search(r"\b(that|this)\s+(job|visit)\b", lower) and context.last_job_id:
            hints.append(f"Resolved visit/job reference: job_id={context.last_job_id}.")

        if re.search(r"\b(weather|forecast|rain)\b", lower) and context.last_job_id:
            hints.append(f"Resolved weather target: job_id={context.last_job_id}.")

        if re.search(r"\bthat\s+invoice\b", lower) and context.last_invoice_id:
            hints.append(f"Resolved invoice reference: invoice_id={context.last_invoice_id}.")

        if re.search(r"\b(she|her|him|his|same customer|that customer)\b", lower) and context.customer_id:
            hints.append(
                "Resolved customer reference: "
                f"customer_id={context.customer_id}"
                + (f" ({context.customer_display_name})" if context.customer_display_name else "")
                + "."
            )

        if not hints:
            return user_message

        return user_message + "\n\n[Session context hints]\n" + "\n".join(f"- {line}" for line in hints)

    def _resolve_effective_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ConversationContext,
        resolved_note_ref: NoteReference | None,
    ) -> tuple[str, dict[str, Any]]:
        if resolved_note_ref is not None and tool_name == "semantic_search_notes":
            return "get_note", {"note_id": resolved_note_ref.note_id}

        if resolved_note_ref is not None and tool_name == "get_note":
            arguments["note_id"] = resolved_note_ref.note_id
        arguments = self._apply_context_argument_defaults(tool_name, arguments, context)
        return tool_name, arguments

    @staticmethod
    def _apply_context_argument_defaults(
        tool_name: str,
        arguments: dict[str, Any],
        context: ConversationContext,
    ) -> dict[str, Any]:
        if tool_name in {"get_customer", "get_customer_jobs", "get_customer_balance", "get_upcoming_appointments"}:
            if not arguments.get("customer_id") and context.customer_id:
                arguments["customer_id"] = context.customer_id

        if tool_name in {"get_job", "get_job_invoice"}:
            if not arguments.get("job_id") and context.last_job_id:
                arguments["job_id"] = context.last_job_id

        if tool_name == "reschedule_appointment":
            if not arguments.get("job_id") and context.last_job_id:
                arguments["job_id"] = context.last_job_id

        if tool_name == "get_weather_forecast" and not arguments.get("job_id") and context.last_job_id:
            arguments["job_id"] = context.last_job_id

        if tool_name == "get_note" and not arguments.get("note_id"):
            if context.recent_note_references:
                arguments["note_id"] = context.recent_note_references[0].note_id

        if tool_name == "semantic_search_notes":
            if not arguments.get("customer_id") and context.customer_id:
                arguments["customer_id"] = context.customer_id
            if not arguments.get("job_id") and context.last_job_id and re.search(
                r"\b(note\s*\d+|first|second|third|that job|that visit)\b",
                arguments.get("query", "").lower(),
            ):
                arguments["job_id"] = context.last_job_id

        return arguments

    def close_session(self, session_id: int) -> None:
        record = self._session_service.get_session(session_id)
        context = ConversationContext.from_metadata(record.metadata_json if record else None)
        self._session_service.update_session(
            session_id=session_id,
            status="completed",
            customer_id=context.customer_id,
            metadata_updates={"conversation_context": context.to_metadata_payload()},
            mark_ended=True,
        )

    @staticmethod
    def _apply_grounding_safeguards(
        response_text: str,
        context: ConversationContext,
        tool_calls: list[AgentToolCall],
    ) -> str:
        lower = response_text.lower()
        notes_text = " ".join(ref.content_preview.lower() for ref in context.recent_note_references)
        evidence_status = AgentOrchestrator._classify_note_evidence(context)
        uses_note_evidence = any(call.tool_name in {"semantic_search_notes", "get_note"} for call in tool_calls)

        if not uses_note_evidence:
            return response_text

        needs_causality_guard = any(
            marker in lower
            for marker in [" caused ", "cause of", "which explains", "associated with", "led to"]
        )
        evidence_has_causality = any(
            marker in notes_text
            for marker in [" caused ", "because", "due to", "led to", "associated with", "cause"]
        )

        updates: list[str] = []
        if needs_causality_guard and not evidence_has_causality:
            updates.append("The available notes do not establish a causal relationship between those issues.")

        uncertain_markers = ["possible leak", "suspected leak", "could not locate leak", "could not find the leak"]
        evidence_is_uncertain = any(marker in notes_text for marker in uncertain_markers)
        overconfident_claim = any(marker in lower for marker in ["confirmed leak", "leak location was", "was a leak at"])

        if evidence_is_uncertain and overconfident_claim:
            updates.append("The records describe this as possible/suspected and do not confirm the leak location.")

        if evidence_status != EvidenceStatus.EXPLICIT_CONFIRMATION and AgentOrchestrator._looks_like_overclaim(response_text):
            updates.append(AgentOrchestrator._evidence_correction_sentence(evidence_status, context))

        if not updates:
            return response_text

        if AgentOrchestrator._looks_like_overclaim(response_text):
            return " ".join(updates)

        return response_text.rstrip() + " " + " ".join(updates)

    @staticmethod
    def _classify_note_evidence(context: ConversationContext) -> EvidenceStatus:
        note_texts = [ref.content_preview.lower() for ref in context.recent_note_references if ref.content_preview]
        if not note_texts:
            return EvidenceStatus.INSUFFICIENT_EVIDENCE

        if any(AgentOrchestrator._matches_any_pattern(text, _EVIDENCE_CONFIRMATION_PATTERNS) for text in note_texts):
            return EvidenceStatus.EXPLICIT_CONFIRMATION

        if any(AgentOrchestrator._matches_any_pattern(text, _EVIDENCE_NEGATION_PATTERNS) for text in note_texts):
            return EvidenceStatus.EXPLICIT_NEGATION

        if any(AgentOrchestrator._matches_any_pattern(text, _EVIDENCE_POSSIBLE_PATTERNS) for text in note_texts):
            return EvidenceStatus.POSSIBLE_OR_SUSPECTED

        if any(AgentOrchestrator._matches_any_pattern(text, _EVIDENCE_RELATED_PATTERNS) for text in note_texts):
            return EvidenceStatus.RELATED_BUT_NOT_CONFIRMING

        return EvidenceStatus.INSUFFICIENT_EVIDENCE

    @staticmethod
    def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _looks_like_overclaim(response_text: str) -> bool:
        lower = response_text.lower()
        if any(re.search(pattern, lower) for pattern in _NON_OVERCLAIM_PATTERNS):
            return False
        return any(re.search(pattern, lower) for pattern in _OVERCLAIM_PATTERNS)

    @staticmethod
    def _evidence_correction_sentence(status: EvidenceStatus, context: ConversationContext) -> str:
        snippets = [ref.content_preview for ref in context.recent_note_references if ref.content_preview]
        snippet = snippets[0] if snippets else None

        if status == EvidenceStatus.EXPLICIT_NEGATION:
            if snippet and "leak" in snippet.lower():
                base = "The technician performed a leak search, but the available records do not confirm a leak."
            else:
                base = "The technician performed a search, but the available records do not confirm the claim."
        elif status == EvidenceStatus.POSSIBLE_OR_SUSPECTED:
            base = "The records suggest a possible issue, but they do not confirm the claim."
        elif status == EvidenceStatus.RELATED_BUT_NOT_CONFIRMING:
            base = "The records are related to the topic, but they do not establish the claim."
        else:
            base = "The available records do not clearly confirm the claim."

        if snippet:
            return f'{base} Evidence: "{snippet}".'
        return base


def build_default_agent() -> AgentOrchestrator:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to run agent CLI")

    embedding_provider = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model_name=settings.openai_embedding_model,
    )
    semantic_retriever = PgVectorSemanticRetriever(
        SessionLocal,
        embedding_provider,
        expected_dimension=settings.embedding_dimension,
        timeout=settings.embedding_timeout,
        max_top_k=settings.semantic_max_top_k,
    )

    registry = build_default_tool_registry(SessionLocal, semantic_retriever)
    model = OpenAIToolCallingModel(
        api_key=settings.openai_api_key,
        model_name=settings.openai_agent_model,
    )
    return AgentOrchestrator(
        model=model,
        registry=registry,
        session_service=AgentSessionService(SessionLocal),
        action_service=AgentActionService(SessionLocal),
        max_tool_rounds=settings.agent_max_tool_rounds,
        max_history_messages=settings.agent_max_history_messages,
    )
