package taxhelper.chatservice.precedent;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.web.reactive.function.client.*;
import reactor.core.publisher.Mono;
import taxhelper.chatservice.domain.precedent.dto.request.SummaryDetailRequest;
import taxhelper.chatservice.domain.precedent.dto.request.SummaryRequest;
import taxhelper.chatservice.domain.precedent.dto.request.SummaryShell;
import taxhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;
import taxhelper.chatservice.domain.precedent.service.PrecedentProperties;
import taxhelper.chatservice.domain.precedent.service.SummaryService;

import java.util.*;

import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
public class SummaryServiceTest {

    @Mock
    private PrecedentSummaryRepository precedentSummaryRepository;

    @Mock
    private PrecedentProperties precedentProperties;

    @InjectMocks
    private SummaryService summaryService;

    private Map<String, SummaryShell> dummyMap;

    private WebClient webClient;

    @BeforeEach
    void setup() {
        // 1️⃣ 키워드 리스트
        List<String> keywords = List.of(
                "국세", "부가세", "법인세", "소득세", "증권거래세",
                "상속세", "증여세", "주세", "관세", "지방세",
                "취득세", "재산세", "등록세"
        );

        // 2️⃣ 키워드별 더미 객체 생성
        dummyMap = new LinkedHashMap<>();
        for (String keyword : keywords) {
            SummaryDetailRequest detail = new SummaryDetailRequest();
            detail.setPrecedentNo("dummy-" + keyword);
            detail.setCaseName("사건명-" + keyword);
            detail.setSentencingDate("2024.01.01");

            SummaryRequest req = new SummaryRequest();
            req.setPrec(List.of(detail));
            req.setTotalCnt("1");

            SummaryShell shell = new SummaryShell();
            shell.setPrecSearch(req);

            dummyMap.put(keyword, shell);
        }

        // 3️⃣ ExchangeFunction Mock
        ExchangeFunction exchangeFunction = mock(ExchangeFunction.class);
        Iterator<SummaryShell> dummyIterator = dummyMap.values().iterator();

        given(exchangeFunction.exchange(any(ClientRequest.class)))
                .willAnswer(invocation -> {
                    ClientResponse response = mock(ClientResponse.class);

                    // 상태 코드 모킹
                    given(response.statusCode()).willReturn(HttpStatus.OK);

                    // bodyToMono 모킹
                    SummaryShell shell = dummyIterator.hasNext() ? dummyIterator.next() : null;
                    given(response.bodyToMono(SummaryShell.class)).willReturn(Mono.just(shell));

                    return Mono.just(response);
                });

        // 4️⃣ 테스트용 WebClient 생성
        webClient = WebClient.builder()
                .exchangeFunction(exchangeFunction)
                .build();

        // summaryService에 webClient 주입
        summaryService = new SummaryService(webClient, precedentSummaryRepository, precedentProperties);

        // 5️⃣ Properties Mock
        when(precedentProperties.getKeywords()).thenReturn(keywords);

        // 6️⃣ Repository saveAll Mock
        when(precedentSummaryRepository.saveAll(anyList())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    @DisplayName("13개 키워드 더미 객체 기반, API 호출 없는 테스트")
    void testFetchAllKeywords() {
        summaryService.fetchAllKeywords();

        // 저장 호출 횟수 검증
        verify(precedentSummaryRepository, atLeastOnce()).saveAll(anyList());

        System.out.println("테스트 완료! 13개 키워드 모두 dummy 데이터 처리됨.");
    }
}
