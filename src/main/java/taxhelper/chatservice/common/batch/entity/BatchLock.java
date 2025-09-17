package taxhelper.chatservice.common.batch.entity;

import jakarta.persistence.*;
import lombok.Getter;

import java.time.LocalDateTime;

@Entity
@Getter
@Table(name = "batch_lock")
public class BatchLock {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_name", nullable = false, unique = true)
    private String jobName;

    @Column(name = "locked_at", nullable = false)
    private LocalDateTime lockedAt;

    @Column(name = "locked_by", nullable = false)
    private String lockedBy;

    public void setJobName(String jobName) {
        this.jobName = jobName;
    }

    public void setLockedAt(LocalDateTime now) {
        this.lockedAt = now;
    }

    public void setLockedBy(String instanceId) {
        this.lockedBy = instanceId;
    }
}
