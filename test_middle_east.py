from bot import parse_ticket

subject = "[ESP][AR] Incident INC0000774654 has been assigned to your group"
body = "Location: UAE\n"
res = parse_ticket(subject, body, country_tag="", is_middle_east=True)
print("Result 1:", res)

subject = "[ESP][AR] Requested Item RITM0002221017 has been created and assigned to your group"
res = parse_ticket(subject, body, country_tag="", is_middle_east=True)
print("Result 2:", res)

subject = "Response SLA BREACHED - Incident INC0000774654"
res = parse_ticket(subject, body, country_tag="", is_middle_east=True)
print("Result 3:", res)
