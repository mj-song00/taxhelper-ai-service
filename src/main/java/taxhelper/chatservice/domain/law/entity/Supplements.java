package taxhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import taxhelper.chatservice.common.entity.Timestamped;

@Getter
@Entity
@Table(name = "supplements")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 부칙을 supplements로 분리하여 테이블로 만들었습니다.
 */
public class Supplements extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long supplementsId;

    @Column
    private Long proclamationNo;

    @Column
    private String proclamationDate;

    @Column(columnDefinition = "text")
    private String content;

    @ManyToOne
    @JoinColumn(name = "lawId", nullable = false)
    private Law law;

    public static Supplements of(Long supplementsId, Long proclamationNo,
                                 String proclamationDate, String content, Law law) {
        Supplements supplements = Supplements.builder()
                .supplementsId(supplementsId)
                .proclamationNo(proclamationNo)
                .proclamationDate(proclamationDate)
                .content(content)
                .law(law)
                .build();
        law.getSupplements().add(supplements);
        return supplements;
    }
}
