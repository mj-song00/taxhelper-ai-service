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
@Table(name = "paragraph")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 항을 paragraph로 분리하여 테이블로 만들었습니다.
 */
public class Paragraph extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long paragraphId;

    @Column
    private String pargraphNo;

    @Column
    private String revisionType;

    @Column
    private String revisionTypeStr;

    @Column(columnDefinition = "text")
    private String content;

    @OneToMany(mappedBy ="paragraph", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Item> item = new ArrayList<>();

    @ManyToOne
    @JoinColumn(name = "articleId", nullable = false)
    private Articles article;

    public void assignArticle(Articles article){
        this.article = article;
    }

    public Paragraph addItem(Item i){
        if (this.item == null) this.item = new ArrayList<>();
        this.item.add(i);
        i.assignItem(this);
        return this;
    }
}
