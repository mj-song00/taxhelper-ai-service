package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Getter
@Entity
@Table(name = "articles")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 * https://open.law.go.kr/LSO/openApi/guideResult.do
 * 출력결과 필드중 조문에 해당하는 부분을 분리하여 만들었습니다.
 */
public class Articles extends Timestamped { // 법령과 조문은 1:N
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long articleId;

    @Column
    private String articleNo;

    @Column
    private String title;

    @Column(columnDefinition = "text")
    private String content;

    @Column
    private String enforcementDate;

    @Column
    private String isChanged;

    @Column
    private String moveBefore;

    @Column
    private String moveAfter;

    @Column(columnDefinition = "TEXT", nullable = true)
    private String reference;

    @Column
    private String articleType;

    @Column
    private String branchNo;


    @OneToMany(cascade = CascadeType.ALL, orphanRemoval = true)
    @JoinColumn(name = "articleId")
    private List<Paragraph> paragraph =  new ArrayList<>();

    @ManyToOne
    @JoinColumn(name = "lawId", nullable = false)
    private Law law;


    public Articles addParagraph(Paragraph p){
        if (this.paragraph == null) {
            this.paragraph = new ArrayList<>();
        }
        this.paragraph.add(p);
        p.assignArticle(this); // Paragraph 안에서만 내부 필드 연결
        return this;
    }


    public static  Articles of (Long articleId, String articleNo, String title,
                                String content, String enforcementDate, String isChanged,
                                String moveBefore, String moveAfter, String reference, String articleType,
                                String branchNo, List<Paragraph> paragraph, Law law) {
        Articles articles =  Articles.builder()
                .articleId(articleId)
                .articleNo(articleNo)
                .title(title)
                .content(content)
                .enforcementDate(enforcementDate)
                .isChanged(isChanged)
                .moveBefore(moveBefore)
                .moveAfter(moveAfter)
                .reference(reference)
                .articleType(articleType)
                .branchNo(branchNo)
                .law(law)
                .build();

        // 양방향 연관관계 세팅
        for (Paragraph p : paragraph) {
            articles.addParagraph(p);
        }

        // 법령에도 조문 추가
        law.getArticles().add(articles);

        return articles;

    }
}
