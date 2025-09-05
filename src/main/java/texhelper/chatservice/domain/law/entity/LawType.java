package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

@Getter
@Entity
@Table(name = "law_type")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 소관부처를 departmentUnit으로 분리하여 테이블로 만들었습니다.
 */

public class LawType extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long lawTypeId;

    @Column
    private String lawType;

    @Column
    private String lawTypeCode;

    @ManyToOne
    @JoinColumn(name = "lawId", nullable = false)
    private Law law;

    public void assignLawType(Law law) {
        this.law = law;
    }
}
