package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@Entity
@Table(name = "lawType")
@NoArgsConstructor
@AllArgsConstructor
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 소관부처를 departmentUnit으로 분리하여 테이블로 만들었습니다.
 */

public class LawType {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long lawTypeId;

    @Column
    private String lawType;

    @Column
    private String lawTypeCode;
}
