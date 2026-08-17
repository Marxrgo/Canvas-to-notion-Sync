import os
import sys
import requests
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv(".env.private")
except ImportError:
    pass  # .env loading is optional; you can also export the vars directly

# ---------------------------------------------------------------------------
# Config (all pulled from environment variables -- (see envExample.txt)
# ---------------------------------------------------------------------------

CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "").rstrip("/")  # e.g. https://yourschool.instructure.com
CANVAS_TOKEN = os.environ.get("CANVAS_TOKEN", "")
SCHOOL_YEAR_START = datetime(2025,8,1,tzinfo = timezone.utc) # Edit this for your school year Start date yyyy,mm,dd in utc

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_COURSES_DATABASE_ID = os.environ.get("NOTION_COURSES_DATABASE_ID", "")  # optional

NOTION_VERSION = "2026-03-11"

REQUIRED_VARS = {
    "CANVAS_BASE_URL": CANVAS_BASE_URL,
    "CANVAS_TOKEN": CANVAS_TOKEN,
    "NOTION_TOKEN": NOTION_TOKEN,
    "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
}

HIDDEN_COURSES = [course.strip() for course in os.environ.get("IGNORED_COURSE","").split(",") if course.strip()]


def check_config():
    missing = [k for k, v in REQUIRED_VARS.items() if not v]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        print("see envExample and README.md")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

def canvas_get(path, params=None):
    """GET helper against the Canvas API. Handles pagination automatically."""
    url = f"{CANVAS_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    results = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        results.extend(resp.json())
        url = None
        params = None
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part[part.find("<") + 1: part.find(">")]
    return results


def get_active_courses():
    return canvas_get("/api/v1/courses", params={"enrollment_state": "active", "per_page": 100})


def get_assignments_for_course(course_id):
    return canvas_get(
        f"/api/v1/courses/{course_id}/assignments",
        params={"per_page": 100, "order_by": "due_at", "include[]": "submission"},
    )


def fetch_all_assignments():
    """Returns a flat list of dicts: one per assignment, with course name attached."""
    assignments = []
    for course in get_active_courses():
        if course.get("name","Unknown course") in HIDDEN_COURSES:
                    #Skip your HIDDEN_COURSES -- (see envExample.txt)
                    continue
        course_name = course.get("name", "Unknown course")
        course_id = course["id"]
        try:
            course_assignments = get_assignments_for_course(course_id)
        except requests.HTTPError:
            # Some courses (e.g. ones without assignments enabled) will 404/403; skip them.
            continue
        for a in course_assignments:
            # Skip assignments with no due date at all, or assighments from before SCHOOL_YEAR_START
            if not a.get("due_at"):
                continue
            elif SCHOOL_YEAR_START > datetime.fromisoformat(a.get("due_at")):
                continue

            submission = a.get("submission") or {}
            assignments.append({
                "canvas_id": str(a["id"]),
                "title": a.get("name", "Untitled assignment"),
                "course": course_name,
                "due_at": a.get("due_at"),  # ISO 8601 string, UTC
                "html_url": a.get("html_url", ""),
                "submission": submission.get("workflow_state"),
                "on_paper": "on_paper" in (a.get("submission_types") or []),
                "score": submission.get("score"),
                "points_possible": a.get("points_possible")
            })
    return assignments


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


_data_source_id_cache = None
_courses_data_source_id_cache = None
_course_page_cache = {}


def get_data_source_id():
    global _data_source_id_cache
    if _data_source_id_cache:
        return _data_source_id_cache
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
    resp = requests.get(url, headers=NOTION_HEADERS, timeout=30)
    resp.raise_for_status()
    data_sources = resp.json().get("data_sources", [])
    if not data_sources:
        raise RuntimeError(
            "No data sources found for this database ID. Double-check "
            "NOTION_DATABASE_ID and that the integration has access to it."
        )
    _data_source_id_cache = data_sources[0]["id"]
    return _data_source_id_cache


def notion_find_existing_page(canvas_id):
    """Look up a row by the CanvasID property. Returns page id or None."""
    url = f"https://api.notion.com/v1/data_sources/{get_data_source_id()}/query"
    payload = {
        "filter": {
            "property": "CanvasID",
            "rich_text": {"equals": canvas_id},
        }
    }
    resp = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None

def get_courses_data_source_id():
    global _courses_data_source_id_cache
    if _courses_data_source_id_cache:
        return _courses_data_source_id_cache
    url = f"https://api.notion.com/v1/databases/{NOTION_COURSES_DATABASE_ID}"
    resp = requests.get(url, headers=NOTION_HEADERS, timeout=30)
    resp.raise_for_status()
    data_sources = resp.json().get("data_sources", [])
    if not data_sources:
        raise RuntimeError("No data sources found for NOTION_COURSES_DATABASE_ID.")
    _courses_data_source_id_cache = data_sources[0]["id"]
    return _courses_data_source_id_cache


def find_or_create_course_page(course_name):
    if course_name in _course_page_cache:
        return _course_page_cache[course_name]

    query_url = f"https://api.notion.com/v1/data_sources/{get_courses_data_source_id()}/query"
    payload = {"filter": {"property": "Name", "title": {"equals": course_name}}}
    resp = requests.post(query_url, headers=NOTION_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results", [])

    if results:
        page_id = results[0]["id"]
    else:
        create_payload = {
            "parent": {"type": "data_source_id", "data_source_id": get_courses_data_source_id()},
            "properties": {"Name": {"title": [{"text": {"content": course_name}}]}},
        }
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=create_payload, timeout=30)
        resp.raise_for_status()
        page_id = resp.json()["id"]

    _course_page_cache[course_name] = page_id
    return page_id

def build_properties(assignment):
    properties = {
        "Name": {"title": [{"text": {"content": assignment["title"]}}]},
        "Course": {"select": {"name": assignment["course"][:100]}},
        "Due": {"date": {"start": assignment["due_at"]}},
        "CanvasID": {"rich_text": [{"text": {"content": assignment["canvas_id"]}}]},
        "Link": {"url": assignment["html_url"] or None},
    }

    state = assignment["submission"]

    if state == "graded":
        properties["Done"] = {"checkbox": True}
        properties["Submission"] = {"status": {"name": "Graded"}}
    elif state in ("submitted", "pending_review"):
        properties["Done"] = {"checkbox": True}
        properties["Submission"] = {"status": {"name": "Submitted"}}
    elif not assignment["on_paper"]:
        properties["Submission"] = {"status": {"name": "Unsubmitted"}}
    # else: on_paper and unsubmitted per Canvas -- leave alone, mark by hand

    if assignment["score"] is not None and assignment["points_possible"]:
        grade_pct = assignment["score"] / assignment["points_possible"]
        properties["Grade"] = {"number": grade_pct}   

    return properties


def notion_upsert(assignment):
    properties = build_properties(assignment)

    if NOTION_COURSES_DATABASE_ID:
        course_page_id = find_or_create_course_page(assignment["course"])
        properties["Course Link"] = {"relation": [{"id": course_page_id}]}


    existing_page_id = notion_find_existing_page(assignment["canvas_id"])

    if existing_page_id:
        url = f"https://api.notion.com/v1/pages/{existing_page_id}"
        resp = requests.patch(url, headers=NOTION_HEADERS, json={"properties": properties}, timeout=30)
        action = "updated"
    else:
        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": get_data_source_id()},
            "properties": properties,
        }
        resp = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)
        action = "created"

    resp.raise_for_status()
    return action


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_config()
    print(f"[{datetime.now(timezone.utc).isoformat()}] Fetching Canvas assignments...")
    assignments = fetch_all_assignments()
    print(f"Found {len(assignments)} assignments.")

    created, updated, fail_count = 0, 0, 0
    for a in assignments:
        try:
            action = notion_upsert(a)
            if action == "created":
                created += 1
            else:
                updated += 1
        except requests.HTTPError as e:
            #print(f"  Failed to sync '{a['title']}': {e}")
            fail_count += 1

    if fail_count > 1:
        print(f"Done. {created} created, {updated} updated, {fail_count} failed.")
    else:
        print(f"Done. {created} created, {updated} updated.")


if __name__ == "__main__":
    main()