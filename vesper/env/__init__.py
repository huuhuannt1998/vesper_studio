"""A home in a real place (design 2026-11-03): its site, the sun over it, the weather of its date, the outdoor light
and temperature they make, and what reaches each room through its windows.

Every value carries where it came from, as a product field does: computed (the sun, by astral, as Home Assistant
computes it), recorded (the Open-Meteo archive for the site and hour) or assumed (a clear-sky fallback, a window's
daylight constants), so a result can say which inputs it rests on.
"""
