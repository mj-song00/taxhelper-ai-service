package taxhelper.chatservice.common.batch.service;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import taxhelper.chatservice.common.batch.entity.BatchJobRequest;
import taxhelper.chatservice.common.batch.repository.BatchJobRequestRepository;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class BatchJobRequestService {

    private final BatchJobRequestRepository repository;

    public void createBatchJobRequest(String jobName, String jobParam) {
        BatchJobRequest request = new BatchJobRequest();
        request.setJobName(jobName);
        request.setJobParam(jobParam);
        request.setRequestedAt(LocalDateTime.now());
        repository.save(request);
    }

    public List<BatchJobRequest> getPendingRequests(String jobName) {
        return repository.findByJobNameOrderByRequestedAt(jobName);
    }

    public void deleteRequest(Long requestId) {
        repository.deleteById(requestId);
    }
}
