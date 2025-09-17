package taxhelper.chatservice.common.batch.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import taxhelper.chatservice.common.batch.entity.BatchJobRequest;

import java.util.List;

public interface BatchJobRequestRepository extends JpaRepository<BatchJobRequest, Long> {
    List<BatchJobRequest> findByJobNameOrderByRequestedAt(String jobName);
}
