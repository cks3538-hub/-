"""보고서(report_ko.html/md), model card, JSON schema 내보내기. 외부 리소스/CDN 없음."""

from corp_dl_agent.reporting.html import render_report_ko, render_report_md
from corp_dl_agent.reporting.model_card import build_model_card, write_model_card

__all__ = ["render_report_ko", "render_report_md", "build_model_card", "write_model_card"]
