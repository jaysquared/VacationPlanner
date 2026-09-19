from fast_flights.model import Airline, Airport, Alliance, CarbonEmission, Flights, JsMetadata, SimpleDatetime, SingleFlight
from fast_flights.parser import ResultList


def leg(frm, to, dep, arr, minutes):
    return SingleFlight(
        from_airport=Airport(name=frm, code=frm), to_airport=Airport(name=to, code=to),
        departure=SimpleDatetime(date=dep[:3], time=dep[3:]), arrival=SimpleDatetime(date=arr[:3], time=arr[3:]),
        duration=minutes, plane_type="A350",
    )


def build() -> ResultList:
    rl = ResultList([
        Flights(type="best", price=5940, airlines=["Lufthansa", "Thai"],
                flights=[leg("HAM", "FRA", (2026, 10, 17, 10, 35), (2026, 10, 17, 11, 45), 70),
                         leg("FRA", "BKK", (2026, 10, 17, 13, 55), (2026, 10, 18, 6, 10), 675)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
        Flights(type="other", price=4100, airlines=["Air India"],
                flights=[leg("HAM", "DEL", (2026, 10, 17, 6, 0), (2026, 10, 17, 18, 30), 570),
                         leg("DEL", "BKK", (2026, 10, 17, 21, 0), (2026, 10, 18, 3, 0), 270)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
        Flights(type="other", price=6100, airlines=["Emirates"],
                flights=[leg("HAM", "DXB", (2026, 10, 17, 14, 20), (2026, 10, 17, 23, 5), 405),
                         leg("DXB", "BKK", (2026, 10, 18, 3, 30), (2026, 10, 18, 12, 50), 380)],
                carbon=CarbonEmission(typical_on_route=1, emission=1)),
    ])
    rl.metadata = JsMetadata(
        airlines=[Airline("LH", "Lufthansa"), Airline("TG", "Thai"), Airline("AI", "Air India"), Airline("EK", "Emirates")],
        alliances=[Alliance("STAR_ALLIANCE", "Star Alliance")],
    )
    return rl
