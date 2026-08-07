import unittest

import httpx

from app.services.chunk_client import ChunkSearchClient


class ChunkSearchClientIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.requests: list[httpx.Request] = []

        async def spring_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path.endswith("/law-chunks"):
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "status": 200,
                        "message": "조회에 성공하였습니다.",
                        "data": {
                            "list": [
                                {
                                    "id": 10,
                                    "lawName": "소득세법",
                                    "chunkType": "ARTICLE",
                                    "title": "제52조 특별소득공제",
                                    "content": "장기주택저당차입금 이자상환액",
                                    "sourceId": 52,
                                    "score": 120.5,
                                }
                            ],
                            "currentPage": 1,
                            "totalPages": 2,
                            "totalElements": 11,
                        },
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "list": [
                            {
                                "id": 20,
                                "title": "판결요지",
                                "content": "실질과세 원칙",
                                "chunkType": "SUMMARY",
                                "precedentId": 3,
                                "caseNumber": "2020두12345",
                                "courtName": "대법원",
                                "score": 40.0,
                            }
                        ],
                        "currentPage": 2,
                        "totalPages": 3,
                        "totalElements": 21,
                    }
                },
            )

        self.client = ChunkSearchClient(
            base_url="http://spring.test",
            chunks_path="/api/v1/chunks/law-chunks",
            precedent_chunks_path="/api/v1/chunks/prec-chunks",
            timeout_sec=1.0,
            cache_ttl_sec=0,
        )
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(spring_handler)
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_law_chunk_request_and_spring_response_contract(self) -> None:
        chunks, pagination = await self.client.fetch_chunks(
            candidate_page=1,
            candidate_size=30,
            keywords=["장기주택저당차입금", "공제한도"],
            rewritten_query="이자상환액 공제 한도",
            law_names=["소득세법", "소득세법 시행령"],
        )

        request = self.requests[0]
        self.assertEqual("GET", request.method)
        self.assertEqual("/api/v1/chunks/law-chunks", request.url.path)
        self.assertEqual("1", request.url.params["page"])
        self.assertEqual("30", request.url.params["size"])
        self.assertEqual(
            ["장기주택저당차입금", "공제한도"],
            request.url.params.get_list("keywords"),
        )
        self.assertEqual(
            ["소득세법", "소득세법 시행령"],
            request.url.params.get_list("lawNames"),
        )
        self.assertEqual("제52조 특별소득공제", chunks[0]["title"])
        self.assertEqual(11, pagination["total_elements"])
        self.assertTrue(pagination["has_next"])

    async def test_precedent_request_and_spring_response_contract(self) -> None:
        chunks, pagination = await self.client.fetch_chunks(
            candidate_page=2,
            candidate_size=10,
            keywords=["실질과세"],
            rewritten_query="명의자 실제 귀속자 판례",
            chunks_path="/api/v1/chunks/prec-chunks",
            court_names=["대법원"],
            case_numbers=["2020두12345"],
        )

        request = self.requests[0]
        self.assertEqual("/api/v1/chunks/prec-chunks", request.url.path)
        self.assertEqual(["대법원"], request.url.params.get_list("courtNames"))
        self.assertEqual(
            ["2020두12345"], request.url.params.get_list("caseNumbers")
        )
        self.assertEqual("2020두12345", chunks[0]["caseNumber"])
        self.assertEqual(2, pagination["page"])
        self.assertTrue(pagination["has_next"])


if __name__ == "__main__":
    unittest.main()
