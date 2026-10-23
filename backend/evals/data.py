"""Synthetic lead sheets with known ground truth, for judging the sheet tools and a mission. Nothing here is real data.

Every sheet carries a "Ref" column (a CRM id, as real exports often do) so the answer can be checked: after cleaning and merging, each person must appear exactly once."""
import random

FIRST = ["Aisha", "Omar", "Priya", "Daniel", "Sophie", "Mohammed", "Chloe", "Ravi", "Emma", "Yusuf", "Grace", "Ahmed", "Lucy", "Imran", "Hannah", "Tariq", "Olivia", "Sanjay", "Zara", "Peter",
         "Mary-Ann", "Joseph", "Fatima", "Liam", "Nadia", "Oscar", "Amelia", "Hassan", "Isla", "Kevin", "Leila", "Marcus", "Nina", "Owen", "Rhea", "Samir", "Tara", "Victor", "Wendy", "Zain"]
LAST = ["Khan", "Patel", "Smith", "Jones", "Ali", "Brown", "Taylor", "Hussain", "Wilson", "Ahmed", "Evans", "Begum", "Thomas", "Roberts", "Walker", "Singh", "Wright", "Hall", "Green", "Clarke",
        "McDonald", "O'Neil", "de Silva", "Van der Berg", "Ng", "Osei", "Kowalski", "Rahman", "Nair", "Lewis"]
COURSES = ["Business Management", "Cyber Security", "Data Engineering", "Accounting", "Hospitality Management"]
NOTES = {
    "High": ["Very keen, wants to enrol this month and asked about payment plans", "Ready to start in January, wants the application form", "Keen to sign up, asked how to pay the first instalment",
             "Wants to begin as soon as possible, asked when the next intake is"],
    "Medium": ["Interested but comparing a few colleges, will decide next term", "Likes the course, needs to talk to their employer first", "Wants more details before deciding, maybe in spring",
               "Thinking about it, asked for the brochure and will come back"],
    "Low": ["Just browsing, no plans to study this year", "Not sure it is for them, no timeline", "Only wanted the brochure, says too expensive for now", "Said they signed up by mistake, not interested"],
}
SWITCHBOARD = "020 7946 0000"


def national(i):
    return f"7700{100000 + i:06d}"          # ten digits; with a leading 0 or +44 it is a valid UK mobile


def e164(i):
    return "+44" + national(i)


def phone_form(i, k):
    n = national(i)
    return [f"0{n[:4]} {n[4:]}", f"+44 {n[:4]} {n[4:]}", f"+44{n}", f"(0{n[:4]}) {n[4:]}", f"0044{n}", f"0{n}"][k % 6]


def date_form(rng):
    d, m, y = rng.randint(1, 28), rng.randint(1, 12), 2026
    return rng.choice([f"{d:02d}/{m:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{d} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1]} {y}"])


def person(i, rng):
    first, last = rng.choice(FIRST), rng.choice(LAST)
    label = rng.choice(["High", "Medium", "Low"])
    return {"id": i, "first": first, "last": last, "email": f"{first}.{last}{i}@example.com".lower().replace("'", "").replace(" ", ""), "course": rng.choice(COURSES), "label": label,
            "notes": rng.choice(NOTES[label]), "phone": e164(i)}


def name_form(p, rng):
    full = f"{p['first']} {p['last']}"
    shown = rng.choice([full, full, full.upper(), full.lower()])
    return f"  {shown} " if rng.random() < 0.08 else shown     # stray spaces, as real exports have


def world(seed=7, n_a=1500, n_b_new=330, overlap=270, dups=120, junk=25, switchboard=15, blanks=20, same_name_pairs=30):
    """-> dict(leads=(cols, rows), webinar=(cols, rows), truth={...}, people={id: person})"""
    rng = random.Random(seed)
    people, next_id = {}, 0

    def new_person():
        nonlocal next_id
        p = person(next_id, rng)
        people[p["id"]] = p
        next_id += 1
        return p
    a_people = [new_person() for _ in range(n_a)]
    for k in range(same_name_pairs):               # two different people with exactly the same name: must stay two people
        a, b = a_people[2 * k], a_people[2 * k + 1]
        b["first"], b["last"] = a["first"], a["last"]
        b["email"] = f"{b['first']}.{b['last']}{b['id']}@example.com".lower().replace("'", "").replace(" ", "")
    cols_a = ["Full Name", "Mobile", "E-mail", "Course", "Notes", "Signed up", "Ref"]
    rows_a, has_email = [], {}
    for p in a_people:
        has_email[p["id"]] = rng.random() > 0.15
        rows_a.append([name_form(p, rng), phone_form(p["id"], rng.randint(0, 5)), p["email"] if has_email[p["id"]] else "", p["course"], p["notes"], date_form(rng), str(p["id"])])
    dup_ids = rng.sample(range(n_a), dups)
    for i in dup_ids:                               # the same person again: other phone format, other capitals, sometimes one key missing, but always a key the first row has
        p = people[i]
        drop = rng.choice(["none", "email"] + (["phone"] if has_email[i] else []))
        rows_a.append([name_form(p, rng), "" if drop == "phone" else phone_form(i, rng.randint(0, 5)), "" if drop == "email" else p["email"], p["course"], "", date_form(rng), str(i)])
    junk_with_text = 0
    for _ in range(junk):                           # people with nothing callable: they stay in the list, flagged, never merged
        p = new_person()
        bad = rng.choice(["call me", "12345", "tbc", "n/a", ""])
        junk_with_text += bool(bad)
        rows_a.append([f"{p['first']} {p['last']}", bad, "", p["course"], "no contact details", date_form(rng), str(p["id"])])
    for _ in range(switchboard):                    # a company switchboard shared by fifteen different people: never one person
        p = new_person()
        rows_a.append([f"{p['first']} {p['last']}", SWITCHBOARD, p["email"], p["course"], "works at the same company", date_form(rng), str(p["id"])])
    rows_a += [["", "", "", "", "", "", ""] for _ in range(blanks)]
    rng.shuffle(rows_a)
    cols_b = ["Student name", "Phone number", "Email address", "Attended", "Course", "Ref"]
    rows_b, both_ids, course_conflicts = [], rng.sample(range(n_a), overlap), 0
    for k, i in enumerate(both_ids):                # people already on the first list, found again by phone only, email only, or both
        p = people[i]
        how = ["phone", "email", "both"][k % 3]
        if how == "email" and not has_email[i]:
            how = "phone"                           # a lead can only be recognised by a key the other list also has
        course = p["course"] if rng.random() > 0.1 else rng.choice([c for c in COURSES if c != p["course"]])
        course_conflicts += course != p["course"]
        rows_b.append([name_form(p, rng), phone_form(i, rng.randint(0, 5)) if how != "email" else "", p["email"] if how != "phone" else "", rng.choice(["Yes", "No"]), course, str(i)])
    for _ in range(n_b_new):
        p = new_person()
        rows_b.append([name_form(p, rng), phone_form(p["id"], rng.randint(0, 5)), p["email"], rng.choice(["Yes", "No"]), p["course"], str(p["id"])])
    rng.shuffle(rows_b)
    unique = len(people)
    truth = {"unique_people": unique, "people_on_both_lists": overlap, "extra_rows_in_leads": dups, "blank_rows": blanks, "junk_rows": junk, "switchboard_rows": switchboard,
             "same_name_pairs": same_name_pairs, "leads_rows": len(rows_a), "webinar_rows": len(rows_b), "valid_phone_people": unique - junk, "course_conflicts": course_conflicts, "junk_with_unusable_text": junk_with_text,
             "label_by_phone": {p["phone"]: p["label"] for p in people.values()}, "people_ref_by_phone": {p["phone"]: p["id"] for p in people.values()}}
    return {"leads": (cols_a, rows_a), "webinar": (cols_b, rows_b), "truth": truth, "people": people}


def followup_sheet(w, n=180, seed=3):
    """A short list with only a name, a phone number and free-text notes, for the 'classify every row' task. -> (cols, rows, {phone e164: label})"""
    rng = random.Random(seed)
    chosen = rng.sample([p for p in w["people"].values() if p["id"] < 1500 and p["notes"]], n)
    rows = [[f"{p['first']} {p['last']}", phone_form(p["id"], rng.randint(0, 5)), p["notes"]] for p in chosen]
    return ["Name", "Phone", "Notes"], rows, {p["phone"]: p["label"] for p in chosen}


def campaign_sheet(n=36, seed=11):
    """The list for the mission: n callable people, three of them twice, two with no usable number. -> (cols, rows)"""
    rng = random.Random(seed)
    first = rng.sample(FIRST, 20)
    rows = []
    for i in range(n):
        rows.append([f"{rng.choice(first)} {rng.choice(LAST)}", phone_form(50000 + i, rng.randint(0, 5)), f"lead{i}@example.com" if i % 2 else ""])
    for i in range(3):
        rows.append([rows[i][0].upper(), phone_form(50000 + i, 2), ""])
    rows += [["Nobody Known", "call me", ""], ["Bad Number", "12345", ""]]
    rng.shuffle(rows)
    return ["Name", "Phone", "Email"], rows


INSTRUCTIONS = """Project brief: Level 5 Business Management diploma outreach for AA London College.

Call each lead on behalf of AA London College and tell them about our Level 5 Diploma in Business Management. It is fully online with tutor support.
The fee is 950 pounds in total, which can be paid in monthly instalments. Do not quote any other price.

Ask whether they would like the course details. If they do, text them the details and the link. If they ask for a callback, agree a time and note it.
If they say they are not interested, thank them and end the call politely. If they say stop or ask not to be called, never call them again.

Only call between 10:00 and 18:00 on weekdays. Be friendly, short and natural. Never promise a job or a guaranteed result. Never pretend to be a person if asked directly.
Try each person up to two times. At the end I want a report of how many were reached, how many are interested, and what put people off."""
