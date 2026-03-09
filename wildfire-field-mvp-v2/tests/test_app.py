from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from app.main import app


KML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>uploaded-fireline</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              128.606,37.446,0 128.614,37.446,0 128.614,37.454,0 128.606,37.454,0 128.606,37.446,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""

GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="demo" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>closure-track</name>
    <trkseg>
      <trkpt lat="37.455" lon="128.609" />
      <trkpt lat="37.445" lon="128.611" />
    </trkseg>
  </trk>
</gpx>
"""


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.post("/api/reset-demo")

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_list_layers(self) -> None:
        response = self.client.get("/api/layers")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("layers", payload)
        names = {item["name"] for item in payload["layers"]}
        self.assertIn("roads", names)
        self.assertIn("shelters", names)

    def test_config_upload_formats(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn(".kml", data["acceptedUploadFormats"])
        self.assertIn(".gpx", data["acceptedUploadFormats"])

    def test_route(self) -> None:
        payload = {
            "start": {"lat": 37.45, "lng": 128.6},
            "goal_layer": "shelters",
            "night_mode": True,
            "max_candidates": 3
        }
        response = self.client.post("/api/route", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(len(data["routes"]), 1)
        self.assertIn("distance_m", data["routes"][0])
        self.assertIn("connector_distance_m", data["routes"][0])

    def test_route_switches_target_when_blocked(self) -> None:
        base_payload = {
            "start": {"lat": 37.45, "lng": 128.59},
            "goal_layer": "shelters",
            "night_mode": True,
            "max_candidates": 3,
        }
        original = self.client.post("/api/route", json=base_payload).json()
        blocked = self.client.post(
            "/api/route",
            json={**base_payload, "blocked_segment_ids": ["r-10", "r-12", "r-13"]},
        ).json()
        self.assertNotEqual(
            original["resolved_target"]["feature_id"],
            blocked["resolved_target"]["feature_id"],
        )

    def test_route_goal_point_adds_connector_distance(self) -> None:
        payload = {
            "start": {"lat": 37.4502, "lng": 128.5902},
            "goal_point": {"lat": 37.4585, "lng": 128.6205},
            "night_mode": True,
            "max_candidates": 2,
        }
        response = self.client.post("/api/route", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(data["routes"][0]["connector_distance_m"], 0)

    def test_upload_fireline_geojson(self) -> None:
        feature_collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"id": "test-upload"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[128.606, 37.446], [128.614, 37.446], [128.614, 37.454], [128.606, 37.454], [128.606, 37.446]]]
                    }
                }
            ]
        }
        response = self.client.post(
            "/api/incidents/fireline",
            files={"file": ("fireline.geojson", json.dumps(feature_collection), "application/geo+json")}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_upload_fireline_kml(self) -> None:
        response = self.client.post(
            "/api/incidents/fireline",
            files={"file": ("fireline.kml", KML_SAMPLE, "application/vnd.google-earth.kml+xml")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        layer = self.client.get("/api/layers/fireline").json()
        self.assertGreaterEqual(len(layer["features"]), 1)

    def test_upload_closures_gpx(self) -> None:
        response = self.client.post(
            "/api/incidents/closures",
            files={"file": ("closures.gpx", GPX_SAMPLE, "application/gpx+xml")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        layer = self.client.get("/api/layers/closures").json()
        self.assertGreaterEqual(len(layer["features"]), 1)


if __name__ == "__main__":
    unittest.main()
