package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

import java.util.ArrayList;
import java.util.List;

@Getter
@Entity
@Table(name = "item")
@NoArgsConstructor
@AllArgsConstructor
@Builder
/**
 *  * https://open.law.go.kr/LSO/openApi/guideResult.do
 *  *  법령의 호를 Item으로 분리하여 테이블로 만들었습니다.
 */
public class Item extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column
    private Long itemId;

    @Column
    private String itemNo;

    @Column(columnDefinition = "text")
    private String content;

    @OneToMany(mappedBy="item", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<SubItem> subItem =  new ArrayList<>();

    @ManyToOne
    @JoinColumn(name = "paragraphId", nullable = false)
    private  Paragraph paragraph;


    public void assignItem(Paragraph paragraph){
        this.paragraph = paragraph;
    }

    public Item addSubItem(SubItem s){
        if (this.subItem == null) this.subItem = new ArrayList<>();
        this.subItem.add(s);
        s.assignItem(this);
        return this;
    }

}
