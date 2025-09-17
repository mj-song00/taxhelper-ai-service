package taxhelper.chatservice.common.batch.scheduler;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import taxhelper.chatservice.domain.precedent.service.SummaryService;

@Slf4j
@Component
@RequiredArgsConstructor
public class BatchScheduler {
    private final SummaryService summaryService;


    @Scheduled(initialDelay = 5_000, fixedDelay = Long.MAX_VALUE)
    public void runBatch() {
        summaryService.fetchAllKeywords();
    }

}
