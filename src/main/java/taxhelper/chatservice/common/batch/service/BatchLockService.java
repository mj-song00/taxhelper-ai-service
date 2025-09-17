package taxhelper.chatservice.common.batch.service;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import taxhelper.chatservice.common.batch.entity.BatchLock;
import taxhelper.chatservice.common.batch.repository.BatchLockRepository;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class BatchLockService {
    private final BatchLockRepository batchLockRepository;
    private final String instanceId = "INSTANCE_" + System.currentTimeMillis();

    public boolean acquireLock(String jobName) {
        return batchLockRepository.findByJobName(jobName)
                .filter(lock -> lock.getLockedAt().isBefore(LocalDateTime.now().minusMinutes(5)))
                .map(this::updateLock)
                .orElseGet(() -> createLock(jobName));
    }

    private boolean createLock(String jobName) {
        BatchLock lock = new BatchLock();
        lock.setJobName(jobName);
        lock.setLockedAt(LocalDateTime.now());
        lock.setLockedBy(instanceId);
        batchLockRepository.save(lock);
        return true;
    }

    private boolean updateLock(BatchLock lock) {
        lock.setLockedAt(LocalDateTime.now());
        batchLockRepository.save(lock);
        return true;
    }

    public void releaseLock(String jobName) {
        batchLockRepository.findByJobName(jobName)
                .filter(lock -> lock.getLockedBy().equals(instanceId))
                .ifPresent(batchLockRepository::delete);
    }
}
