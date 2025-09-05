package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

@Getter
@Entity
@Table(name = "department_unit")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 부서단위를 departmentUnit으로 분리하여 테이블로 만들었습니다.
 */
public class DepartmentUnit extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long departmentUnitId;

    @Column
    private String deptName;

    @Column
    private String deptPhone;

    @Column
    private String deptKey;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "ministriesId", nullable = false)
    private Ministry ministries;


    public void assignMinistry(Ministry ministries){
        this.ministries = ministries;
    }
}
