package texhelper.chatservice.common.batch.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import texhelper.chatservice.common.batch.entity.BatchLock;

import java.util.Optional;

public interface BatchLockRepository extends JpaRepository<BatchLock, Long> {
    Optional<BatchLock> findByJobName(String jobName);
}
