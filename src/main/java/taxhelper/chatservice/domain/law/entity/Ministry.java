package taxhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import taxhelper.chatservice.common.entity.Timestamped;

import java.util.ArrayList;
import java.util.List;

@Getter
@Entity
@Table(name = "ministries")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 소관부처를 departmentUnit으로 분리하여 테이블로 만들었습니다.
 */
public class Ministry extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long ministriesId;

    @Column
    private String ministryName; //소관부처명

    @Column
    private String ministryCode;

    @ManyToOne
    @JoinColumn(name = "lawId", nullable = false)
    private Law law;

    @OneToMany(mappedBy ="ministries", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<DepartmentUnit> departmentUnit =  new ArrayList<>();


    public  Ministry addDepartmentUnit (DepartmentUnit d){
        if(this.departmentUnit == null){
            this.departmentUnit = new ArrayList<>();
        }

        this.departmentUnit.add(d);
        d.assignMinistry(this);
        return this;
    }

    public static Ministry of (Long ministriesId, String ministryName, String ministryCode, Law law, List<DepartmentUnit> units ){
        Ministry ministry = Ministry.builder()
                .ministriesId(ministriesId)
                .ministryName(ministryName)
                .ministryCode(ministryCode)
                .law(law)
                .departmentUnit(new ArrayList<>())
                .build();

        for (DepartmentUnit d : units ) { // ✅ ministry 객체를 통해 접근
            ministry.addDepartmentUnit(d);
        }

        law.initMinistryList();
        law.getMinistry().add(ministry);
        return ministry;
    }

}
