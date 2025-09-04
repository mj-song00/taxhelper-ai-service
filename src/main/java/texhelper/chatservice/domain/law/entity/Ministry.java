package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.ArrayList;
import java.util.List;

@Getter
@Entity
@Table(name = "ministries")
@NoArgsConstructor
@AllArgsConstructor
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 소관부처를 departmentUnit으로 분리하여 테이블로 만들었습니다.
 */
public class Ministry {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long ministriesId;

    @Column
    private String ministryName;

    @OneToMany(cascade = CascadeType.ALL, orphanRemoval = true)
    @JoinColumn(name = "ministriesId")
    private List<DepartmentUnit> departmentUnit =  new ArrayList<>();

}
