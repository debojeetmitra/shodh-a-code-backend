# Standard deterministic fixture keys and IDs for evaluation and testing

FIXTURE_ORGS = {
    "org_alpha": {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Alpha Institute of Technology",
        "slug": "alpha-tech"
    },
    "org_beta": {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Beta National University",
        "slug": "beta-univ"
    }
}

FIXTURE_USERS = {
    # Alpha Org Users
    "alpha_organizer": {
        "id": "aaaa0001-0000-0000-0000-000000000001",
        "organization_id": FIXTURE_ORGS["org_alpha"]["id"],
        "username": "alice_organizer",
        "email": "alice@alpha.edu",
        "role": "ORGANIZER",
        "api_key": "sec_alpha_organizer_key_9999"
    },
    "alpha_student_1": {
        "id": "aaaa0002-0000-0000-0000-000000000002",
        "organization_id": FIXTURE_ORGS["org_alpha"]["id"],
        "username": "bob_coder",
        "email": "bob@alpha.edu",
        "role": "PARTICIPANT",
        "api_key": "sec_alpha_student1_key_8888"
    },
    "alpha_student_2": {
        "id": "aaaa0003-0000-0000-0000-000000000003",
        "organization_id": FIXTURE_ORGS["org_alpha"]["id"],
        "username": "charlie_coder",
        "email": "charlie@alpha.edu",
        "role": "PARTICIPANT",
        "api_key": "sec_alpha_student2_key_7777"
    },
    # Beta Org Users
    "beta_organizer": {
        "id": "bbbb0001-0000-0000-0000-000000000001",
        "organization_id": FIXTURE_ORGS["org_beta"]["id"],
        "username": "diana_organizer",
        "email": "diana@beta.edu",
        "role": "ORGANIZER",
        "api_key": "sec_beta_organizer_key_6666"
    },
    "beta_student_1": {
        "id": "bbbb0002-0000-0000-0000-000000000002",
        "organization_id": FIXTURE_ORGS["org_beta"]["id"],
        "username": "eve_hacker",
        "email": "eve@beta.edu",
        "role": "PARTICIPANT",
        "api_key": "sec_beta_student1_key_5555"
    }
}
