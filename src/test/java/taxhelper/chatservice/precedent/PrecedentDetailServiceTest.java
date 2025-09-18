package taxhelper.chatservice.precedent;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClient.ResponseSpec;
import org.springframework.web.reactive.function.client.WebClient.RequestHeadersSpec;
import org.springframework.web.reactive.function.client.WebClient.RequestHeadersUriSpec;
import reactor.core.publisher.Mono;
import taxhelper.chatservice.domain.precedent.detail.dto.DetailServiceRequest;
import taxhelper.chatservice.domain.precedent.detail.dto.DetailShellRequest;
import taxhelper.chatservice.domain.precedent.detail.entity.PrecedentDetail;
import taxhelper.chatservice.domain.precedent.detail.repository.PrecedentDetailRepository;
import taxhelper.chatservice.domain.precedent.detail.service.PrecedentDetailService;
import taxhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
public class PrecedentDetailServiceTest {

    @Mock
    private PrecedentDetailRepository precedentDetailRepository;

    @Mock
    private PrecedentSummaryRepository precedentSummaryRepository;

    @Mock
    private WebClient webClient;

    @InjectMocks
    private PrecedentDetailService precedentDetailService;

    private DetailShellRequest detailShellRequest;

    @BeforeEach
    void setup() {
        // DetailShellRequest 더미 생성
        detailShellRequest = new DetailShellRequest();
        DetailServiceRequest precService = new DetailServiceRequest();

        precService.setCaseName("테스트 사건명");
        precService.setSummaryOfJudgment("테스트 판결요지");
        precService.setCastTypeName("세무");
        precService.setCourtName("대법원");
        precService.setCaseNo("2024두65911");
        precService.setCaseCode("12345");
        precService.setDecision("테스트 판결");
        precService.setReferencePrecedent("참조판례");
        precService.setReferenceClause("참조조문");
        precService.setSentencingDate("20250515");
        detailShellRequest.setPrecService(precService);
    }

    @Test
    @DisplayName("판례 세부 내용 저장, API 호출 없음")
    void testFetchPrecedentDetail() {
        // 1. precedentSummaryRepository에서 precedentNo 리스트 Mocking
        when(precedentSummaryRepository.findAllPrecedentNo())
                .thenReturn(List.of("123"));

        // 2. WebClient Mocking
        RequestHeadersUriSpec<?> uriSpec = mock(RequestHeadersUriSpec.class);
        RequestHeadersSpec<?> headersSpec = mock(RequestHeadersSpec.class);
        ResponseSpec responseSpec = mock(ResponseSpec.class);

        // doReturn() 패턴으로 안전하게 모킹
        doReturn(uriSpec).when(webClient).get();
        doReturn(headersSpec).when(uriSpec).uri(anyString());
        doReturn(responseSpec).when(headersSpec).retrieve();
        doReturn(Mono.just(detailShellRequest)).when(responseSpec).bodyToMono(DetailShellRequest.class);

        // 3. 저장 시 동작 모킹
        when(precedentDetailRepository.save(any(PrecedentDetail.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        // 4. 서비스 메서드 호출
        precedentDetailService.fetchDetails();

        // 5. 저장 동작 검증
        ArgumentCaptor<PrecedentDetail> captor = ArgumentCaptor.forClass(PrecedentDetail.class);
        verify(precedentDetailRepository, atLeastOnce()).save(captor.capture());

        PrecedentDetail savedEntity = captor.getValue();
        assertEquals("123", savedEntity.getPrecedentNo());
        assertEquals("테스트 사건명", savedEntity.getCaseName());
        assertEquals("테스트 판결요지", savedEntity.getSummaryOfJudgment());

        System.out.println("dummy 데이터 처리 완료");
    }
}
