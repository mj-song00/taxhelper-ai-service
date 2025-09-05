package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@Entity
@Table(name = "amendments")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 개정문을 supplements로 분리하여 테이블로 만들었습니다.
 */
public class Amendments {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long amendmentsId;

    @Column
    private String title;

    @Column(columnDefinition = "text")
    private String content;

    @ManyToOne
    @JoinColumn(name = "lawId", nullable = false)
    private Law law;

    public static Amendments of(String title, String content, Law law) {
        if (law == null) {
            throw new IllegalArgumentException("Amendments는 반드시 Law에 속해야 합니다.");
        }

        Amendments amendment = Amendments.builder()
                .title(title)
                .content(content)
                .law(law) // Law 객체 연결
                .build();
        law.getAmendments().add(amendment);

        return amendment;
    }
}
