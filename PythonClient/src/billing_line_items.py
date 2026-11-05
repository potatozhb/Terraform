"""Resolve missing line selections against contracts supplied with this order."""

from datetime import date


def field(value, *names):
    return next((value[name] for name in names if value.get(name) is not None), None)


def missing_selection(value):
    return value is None or str(value).strip().lower() in {"", "0", "-1", "none", "null"}


def needs_line_item_resolution(order):
    project = field(order, "ProjectId", "project_id")
    try:
        if float(project or 0) > 0:
            return False
    except (ValueError, TypeError):
        pass
    major = field(order, "IsMajorAccount", "is_major_account")
    account = field(order, "MajorAccountName", "MajorCustomerName", "major_customer_name")
    if major is True and str(account).strip().casefold() in {"fcl", "calco"}:
        return False
    if not field(order, "Contracts", "contracts"):
        return False
    return any(missing_selection(field(line, "ContractItemId", "contract_item_id"))
               for line in field(order, "WorkOrderLines", "work_order_lines") or [])


def _date(value):
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return parsed if parsed.year > 1 else None
    except ValueError:
        return None


def _contract_periods(contract, item):
    # Item-specific dates take precedence over broad contract-term dates.
    start = field(item, "ContractStartDate", "contract_start_date")
    end = field(item, "ContractEndDate", "contract_end_date")
    if start is not None or end is not None:
        return [(start, end, "item.ContractStartDate/ContractEndDate")]
    start = field(contract, "ContractStartDate", "StartDate", "start_date")
    end = field(contract, "ContractEndDate", "EndDate", "end_date")
    if start is not None or end is not None:
        return [(start, end, "contract.StartDate/EndDate")]
    term = field(contract, "ContractTerm", "contract_term") or {}
    return [(term.get(prefix + "ContractStartDate"), term.get(prefix + "ContractEndDate"),
             f"ContractTerm.{prefix}ContractStartDate/{prefix}ContractEndDate")
            for prefix in ("SW", "HW")]


def line_item_context(order):
    """Return date-valid candidates; the model must match scope using current remarks.

    No historical order or invented line item can establish the association.
    Service dates are interpreted as supplied; no timezone is inferred.
    """
    result = {
        "remarks": field(order, "RemarksCombined", "remarks_combined", "Remarks", "remarks"),
        "line_items": [],
        "instruction": (
            "For each unselected line, choose a candidate only if the performed work and remarks "
            "clearly match its product/scope and contract conditions. Date validity alone is not coverage. "
            "Include the chosen contractId and contractItemId with a concise reason in usedEvidence.contracts. "
            "The association is inferred, not an original selection. If no candidate matches or the match "
            "is ambiguous, preserve the original IDs and explain why. Never invent an item or change stored data."
        ),
    }
    for index, line in enumerate(field(order, "WorkOrderLines", "work_order_lines") or []):
        item_id = field(line, "ContractItemId", "contract_item_id")
        contract_id = field(line, "ContractId", "contract_id")
        entry = {"line_number": field(line, "LineNumber", "line_number") or index + 1,
                 "original_contract_id": contract_id, "original_contract_item_id": item_id,
                 "line_content": field(line, "CombinedContent", "Content", "content"), "candidates": []}
        result["line_items"].append(entry)
        if not missing_selection(item_id):
            entry["status"] = "already_selected"
            continue
        # Do not use order-creation time as a substitute for when service occurred.
        service_date = next((parsed for name in ("ArrivalTime", "arrival_time", "DispatchTime", "dispatch_time",
                                                 "CompleteTime", "complete_time")
                             if (parsed := _date(line.get(name))) is not None), None)
        entry["service_date"] = service_date.isoformat() if service_date else None
        if service_date is None:
            entry["status"] = "missing_service_date"
            continue
        for contract in field(order, "Contracts", "contracts") or []:
            candidate_contract_id = field(contract, "Id", "ContractId", "contract_id")
            if not missing_selection(contract_id) and str(candidate_contract_id) != str(contract_id):
                continue
            state = field(contract, "Status", "status")
            if isinstance(state, str) and state.strip().casefold() in {"inactive", "cancelled", "canceled", "expired"}:
                continue
            for item in field(contract, "Items", "items") or []:
                candidate_item_id = field(item, "Id", "ContractItemId", "contract_item_id")
                if missing_selection(candidate_contract_id) or missing_selection(candidate_item_id):
                    continue
                periods = []
                for start, end, source in _contract_periods(contract, item):
                    start_date, end_date = _date(start), _date(end)
                    if start_date and end_date and start_date <= service_date <= end_date:
                        periods.append({"start": start, "end": end, "source": source})
                if not periods:
                    continue
                entry["candidates"].append({
                    "contract_id": candidate_contract_id, "contract_item_id": candidate_item_id,
                    "product_name": field(item, "ProductName", "product_name"),
                    "contract_status": state, "date_evidence": periods,
                    "content": field(item, "Content", "content"),
                    "coverage": field(item, "Coverages", "coverages"),
                })
        entry["status"] = "choose_from_candidates" if entry["candidates"] else "no_date_valid_candidates"
    return result
