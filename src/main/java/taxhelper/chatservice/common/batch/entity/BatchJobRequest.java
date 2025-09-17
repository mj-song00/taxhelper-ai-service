package taxhelper.chatservice.common.batch.entity;


import jakarta.persistence.*;
import lombok.Getter;

import java.time.LocalDateTime;

@Entity
@Getter
@Table(name = "batch_job_request")
public class BatchJobRequest {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "job_name", nullable = false)
    private String jobName;

    @Column(name = "job_param")
    private String jobParam;

    @Column(name = "requested_at", nullable = false)
    private LocalDateTime requestedAt;

    public void setJobName(String jobName) {
        this.jobName = jobName;
    }

    public void setJobParam(String jobParam) {
        this.jobParam = jobParam;
    }

    public void setRequestedAt(LocalDateTime now) {
        this.requestedAt = now;
    }
}
